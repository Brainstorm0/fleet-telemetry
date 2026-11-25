###############################################################################
# Dashboard hosting: private S3 bucket + CloudFront (HTTPS) with Origin
# Access Control, so the bucket is never public.
###############################################################################

# Learner Lab note: the bucket is created with the AWS CLI, not aws_s3_bucket.
# The lab's service control policy denies s3:GetBucketObjectLockConfiguration,
# which the Terraform AWS provider calls every time it reads an aws_s3_bucket,
# so that resource can never be created or refreshed in the lab. Every other
# bucket setting below is still managed by Terraform.
locals {
  dashboard_bucket = "${local.name}-dashboard-${data.aws_caller_identity.current.account_id}-${local.suffix}"
}

resource "terraform_data" "dashboard_bucket" {
  input = local.dashboard_bucket

  # create only if missing (us-east-1 needs no LocationConstraint)
  provisioner "local-exec" {
    command = "aws s3api head-bucket --bucket ${self.input} || aws s3api create-bucket --bucket ${self.input} --region us-east-1"
  }

  # empties and deletes the bucket on "terraform destroy" (like force_destroy)
  provisioner "local-exec" {
    when    = destroy
    command = "aws s3 rb s3://${self.input} --force"
  }
}

resource "aws_s3_bucket_public_access_block" "dashboard" {
  bucket                  = terraform_data.dashboard_bucket.output
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "dashboard" {
  bucket = terraform_data.dashboard_bucket.output
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# No bucket versioning: "aws s3 rb --force" (used on destroy) cannot delete old
# object versions. The dashboard source is versioned in git instead.

locals {
  content_types = {
    html = "text/html; charset=utf-8"
    css  = "text/css; charset=utf-8"
    js   = "application/javascript; charset=utf-8"
  }

  # every dashboard file except config.js (that one is generated below)
  dashboard_files = setsubtract(fileset("${path.module}/../dashboard", "*.{html,css,js}"), ["config.js", "config.cloud.js"])

  dashboard_config = templatefile("${path.module}/templates/config.js.tftpl", {
    api_url = aws_apigatewayv2_api.http.api_endpoint
    ws_url  = "${aws_apigatewayv2_api.ws.api_endpoint}/${aws_apigatewayv2_stage.ws.name}"
  })
}

resource "aws_s3_object" "dashboard" {
  for_each      = local.dashboard_files
  bucket        = terraform_data.dashboard_bucket.output
  key           = each.value
  source        = "${path.module}/../dashboard/${each.value}"
  etag          = filemd5("${path.module}/../dashboard/${each.value}")
  content_type  = local.content_types[reverse(split(".", each.value))[0]]
  cache_control = "max-age=60"
}

resource "aws_s3_object" "config" {
  bucket        = terraform_data.dashboard_bucket.output
  key           = "config.js"
  content       = local.dashboard_config
  content_type  = local.content_types["js"]
  cache_control = "no-cache"
}

# Copy for opening the dashboard straight from your disk (if CloudFront is off)
resource "local_file" "cloud_config" {
  content  = local.dashboard_config
  filename = "${path.module}/../dashboard/config.cloud.js"
}

# ---------------------------------------------------------------- HTTPS hosting via API Gateway
# Learner Lab blocks CloudFront OAC and OAI, so CloudFront cannot read a private
# bucket there. Instead a small Lambda behind the HTTP API (TLS, throttling)
# reads the dashboard files from the private bucket and serves them at the
# API's https:// URL. The bucket is never made public.
data "archive_file" "site" {
  type        = "zip"
  source_dir  = "${path.module}/../lambdas/site"
  output_path = "${local.lambda_x}/site.zip"
  excludes    = ["__pycache__"]
}

resource "aws_cloudwatch_log_group" "site" {
  name              = "/aws/lambda/${local.name}-dashboard-site"
  retention_in_days = 7
}

resource "aws_lambda_function" "site" {
  function_name    = "${local.name}-dashboard-site"
  description      = "Serves the dashboard over HTTPS from the private S3 bucket"
  role             = data.aws_iam_role.lab.arn
  runtime          = "python3.12"
  handler          = "handler.lambda_handler"
  filename         = data.archive_file.site.output_path
  source_code_hash = data.archive_file.site.output_base64sha256
  timeout          = 10
  memory_size      = 128

  environment {
    variables = {
      SITE_BUCKET = terraform_data.dashboard_bucket.output
    }
  }

  depends_on = [aws_cloudwatch_log_group.site]
}

resource "aws_apigatewayv2_integration" "site" {
  api_id                 = aws_apigatewayv2_api.http.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.site.invoke_arn
  payload_format_version = "2.0"
}

# "GET /{file}" never shadows /latest, /history, ... : exact routes win.
resource "aws_apigatewayv2_route" "site" {
  for_each  = toset(["GET /", "GET /{file}"])
  api_id    = aws_apigatewayv2_api.http.id
  route_key = each.value
  target    = "integrations/${aws_apigatewayv2_integration.site.id}"
}

resource "aws_lambda_permission" "site" {
  statement_id  = "AllowHttpApiInvokeSite"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.site.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.http.execution_arn}/*"
}


# ---------------------------------------------------------------- CloudFront (optional CDN)
# CloudFront sits in front of the HTTP API above (a custom HTTPS origin), not in
# front of S3, so it needs no Origin Access Control / Identity - both of which
# Learner Lab blocks. The bucket stays private either way. If your lab also
# blocks cloudfront:CreateDistribution, set enable_cloudfront = false and use
# dashboard_url_api instead.
resource "aws_cloudfront_distribution" "dashboard" {
  count       = var.enable_cloudfront ? 1 : 0
  enabled     = true
  comment     = "Fleet telemetry dashboard"
  price_class = "PriceClass_100"

  origin {
    domain_name = replace(aws_apigatewayv2_api.http.api_endpoint, "https://", "")
    origin_id   = "dashboard-api"

    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "https-only"
      origin_ssl_protocols   = ["TLSv1.2"]
    }
  }

  default_cache_behavior {
    target_origin_id       = "dashboard-api"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    compress               = true

    # AWS managed policy "CachingOptimized" (honours our Cache-Control headers)
    cache_policy_id = "658327ea-f89d-4fab-a63d-7e88639e58f6"

    # AWS managed policy "SecurityHeadersPolicy" (HSTS, X-Frame-Options, ...)
    response_headers_policy_id = "67f7725c-6f97-4210-82d7-5512b31e9d03"
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
    minimum_protocol_version       = "TLSv1.2_2021"
  }
}
