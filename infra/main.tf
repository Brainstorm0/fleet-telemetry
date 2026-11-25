###############################################################################
# Core pipeline: Kinesis -> Lambda -> DynamoDB / SNS / WebSocket
# Learner Lab note: every Lambda uses the existing "LabRole" (we cannot
# create IAM roles in Learner Lab).
###############################################################################

data "aws_caller_identity" "current" {}

data "aws_iam_role" "lab" {
  name = var.lab_role_name
}

resource "random_id" "suffix" {
  byte_length = 3
}

# Shared secret for HMAC-SHA256 frame signatures (simulator <-> Lambda)
resource "random_password" "hmac" {
  length  = 40
  special = false
}

locals {
  name     = var.project
  suffix   = random_id.suffix.hex
  lambda_x = "${path.module}/build"
}

# ---------------------------------------------------------------- Kinesis
resource "aws_kinesis_stream" "telemetry" {
  name             = "${local.name}-telemetry-stream"
  shard_count      = 1
  retention_period = var.kinesis_retention_hours
  encryption_type  = "KMS"
  kms_key_id       = "alias/aws/kinesis"

  stream_mode_details {
    stream_mode = "PROVISIONED"
  }
}

# ---------------------------------------------------------------- DynamoDB
resource "aws_dynamodb_table" "telemetry" {
  name         = "${local.name}-FleetTelemetry"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "VehicleId"
  range_key    = "Timestamp"

  attribute {
    name = "VehicleId"
    type = "S"
  }
  attribute {
    name = "Timestamp"
    type = "N"
  }

  ttl {
    attribute_name = "ExpiresAt"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }
}

# Latest state per vehicle, alerts, and e-mail cooldowns (single-table design)
resource "aws_dynamodb_table" "state" {
  name         = "${local.name}-FleetState"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "Pk"
  range_key    = "Sk"

  attribute {
    name = "Pk"
    type = "S"
  }
  attribute {
    name = "Sk"
    type = "S"
  }

  ttl {
    attribute_name = "ExpiresAt"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }
}

resource "aws_dynamodb_table" "connections" {
  name         = "${local.name}-FleetConnections"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "connectionId"

  attribute {
    name = "connectionId"
    type = "S"
  }

  ttl {
    attribute_name = "ExpiresAt"
    enabled        = true
  }

  server_side_encryption {
    enabled = true
  }
}

# ---------------------------------------------------------------- SNS
resource "aws_sns_topic" "alerts" {
  name = "${local.name}-fleet-alerts"
}

resource "aws_sns_topic_subscription" "email" {
  count     = var.alert_email == "" ? 0 : 1
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

# ---------------------------------------------------------------- Lambda code
data "archive_file" "processor" {
  type        = "zip"
  source_dir  = "${path.module}/../lambdas/processor"
  output_path = "${local.lambda_x}/processor.zip"
  excludes    = ["__pycache__"]
}

data "archive_file" "connections" {
  type        = "zip"
  source_dir  = "${path.module}/../lambdas/connections"
  output_path = "${local.lambda_x}/connections.zip"
  excludes    = ["__pycache__"]
}

data "archive_file" "api" {
  type        = "zip"
  source_dir  = "${path.module}/../lambdas/api"
  output_path = "${local.lambda_x}/api.zip"
  excludes    = ["__pycache__"]
}

# Log groups created up-front so we control retention (cost + compliance)
resource "aws_cloudwatch_log_group" "processor" {
  name              = "/aws/lambda/${local.name}-telemetry-processor"
  retention_in_days = 7
}

resource "aws_cloudwatch_log_group" "connections" {
  name              = "/aws/lambda/${local.name}-ws-connections"
  retention_in_days = 7
}

resource "aws_cloudwatch_log_group" "api" {
  name              = "/aws/lambda/${local.name}-rest-api"
  retention_in_days = 7
}

# ---------------------------------------------------------------- Lambda: processor
resource "aws_lambda_function" "processor" {
  function_name    = "${local.name}-telemetry-processor"
  description      = "Verifies, analyses and stores fleet telemetry from Kinesis"
  role             = data.aws_iam_role.lab.arn
  runtime          = "python3.12"
  handler          = "handler.lambda_handler"
  filename         = data.archive_file.processor.output_path
  source_code_hash = data.archive_file.processor.output_base64sha256
  timeout          = 30
  memory_size      = 256

  environment {
    variables = {
      TELEMETRY_TABLE   = aws_dynamodb_table.telemetry.name
      STATE_TABLE       = aws_dynamodb_table.state.name
      CONNECTIONS_TABLE = aws_dynamodb_table.connections.name
      SNS_TOPIC_ARN     = aws_sns_topic.alerts.arn
      WS_ENDPOINT       = "https://${aws_apigatewayv2_api.ws.id}.execute-api.${var.region}.amazonaws.com/${aws_apigatewayv2_stage.ws.name}"
      HMAC_SECRET       = random_password.hmac.result
      CARGO_TEMP_MAX    = tostring(var.cargo_temp_max)
      SPEED_MAX         = tostring(var.speed_max)
      RETENTION_DAYS    = tostring(var.retention_days)
    }
  }

  depends_on = [aws_cloudwatch_log_group.processor]
}

resource "aws_lambda_event_source_mapping" "kinesis" {
  event_source_arn                   = aws_kinesis_stream.telemetry.arn
  function_name                      = aws_lambda_function.processor.arn
  starting_position                  = "LATEST"
  batch_size                         = 50
  maximum_batching_window_in_seconds = 0
  maximum_retry_attempts             = 3
  bisect_batch_on_function_error     = true
  function_response_types            = ["ReportBatchItemFailures"]
}

# ---------------------------------------------------------------- Lambda: websocket connections
resource "aws_lambda_function" "connections" {
  function_name    = "${local.name}-ws-connections"
  description      = "Tracks dashboard WebSocket connections"
  role             = data.aws_iam_role.lab.arn
  runtime          = "python3.12"
  handler          = "handler.lambda_handler"
  filename         = data.archive_file.connections.output_path
  source_code_hash = data.archive_file.connections.output_base64sha256
  timeout          = 10
  memory_size      = 128

  environment {
    variables = {
      CONNECTIONS_TABLE = aws_dynamodb_table.connections.name
    }
  }

  depends_on = [aws_cloudwatch_log_group.connections]
}

# ---------------------------------------------------------------- Lambda: REST API
resource "aws_lambda_function" "api" {
  function_name    = "${local.name}-rest-api"
  description      = "Read-only REST API for the dashboard"
  role             = data.aws_iam_role.lab.arn
  runtime          = "python3.12"
  handler          = "handler.lambda_handler"
  filename         = data.archive_file.api.output_path
  source_code_hash = data.archive_file.api.output_base64sha256
  timeout          = 10
  memory_size      = 128

  environment {
    variables = {
      TELEMETRY_TABLE = aws_dynamodb_table.telemetry.name
      STATE_TABLE     = aws_dynamodb_table.state.name
    }
  }

  depends_on = [aws_cloudwatch_log_group.api]
}

# ---------------------------------------------------------------- API Gateway: WebSocket
resource "aws_apigatewayv2_api" "ws" {
  name                       = "${local.name}-live-ws"
  protocol_type              = "WEBSOCKET"
  route_selection_expression = "$request.body.action"
}

resource "aws_apigatewayv2_integration" "ws" {
  api_id             = aws_apigatewayv2_api.ws.id
  integration_type   = "AWS_PROXY"
  integration_uri    = aws_lambda_function.connections.invoke_arn
  integration_method = "POST"
}

resource "aws_apigatewayv2_route" "ws" {
  for_each  = toset(["$connect", "$disconnect", "$default"])
  api_id    = aws_apigatewayv2_api.ws.id
  route_key = each.value
  target    = "integrations/${aws_apigatewayv2_integration.ws.id}"
}

resource "aws_apigatewayv2_stage" "ws" {
  api_id      = aws_apigatewayv2_api.ws.id
  name        = "prod"
  auto_deploy = true

  default_route_settings {
    throttling_burst_limit = 100
    throttling_rate_limit  = 50
  }
}

resource "aws_lambda_permission" "ws" {
  statement_id  = "AllowWebSocketInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.connections.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.ws.execution_arn}/*"
}

# ---------------------------------------------------------------- API Gateway: HTTP (REST)
resource "aws_apigatewayv2_api" "http" {
  name          = "${local.name}-rest"
  protocol_type = "HTTP"

  cors_configuration {
    allow_origins = ["*"]
    allow_methods = ["GET", "OPTIONS"]
    allow_headers = ["content-type"]
    max_age       = 3600
  }
}

resource "aws_apigatewayv2_integration" "http" {
  api_id                 = aws_apigatewayv2_api.http.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.api.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "http" {
  for_each  = toset(["GET /latest", "GET /history", "GET /alerts", "GET /health"])
  api_id    = aws_apigatewayv2_api.http.id
  route_key = each.value
  target    = "integrations/${aws_apigatewayv2_integration.http.id}"
}

resource "aws_apigatewayv2_stage" "http" {
  api_id      = aws_apigatewayv2_api.http.id
  name        = "$default"
  auto_deploy = true

  default_route_settings {
    throttling_burst_limit = 50
    throttling_rate_limit  = 20
  }
}

resource "aws_lambda_permission" "http" {
  statement_id  = "AllowHttpApiInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.api.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.http.execution_arn}/*"
}
