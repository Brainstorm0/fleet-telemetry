output "dashboard_url" {
  description = "Open this in your browser (HTTPS)."
  value       = var.enable_cloudfront ? "https://${aws_cloudfront_distribution.dashboard[0].domain_name}" : "${aws_apigatewayv2_api.http.api_endpoint}/"
}

output "dashboard_url_api" {
  description = "Same dashboard served by API Gateway + Lambda from the private bucket (works without CloudFront)."
  value       = "${aws_apigatewayv2_api.http.api_endpoint}/"
}

output "websocket_url" {
  value = "${aws_apigatewayv2_api.ws.api_endpoint}/${aws_apigatewayv2_stage.ws.name}"
}

output "rest_api_url" {
  value = aws_apigatewayv2_api.http.api_endpoint
}

output "kinesis_stream" {
  value = aws_kinesis_stream.telemetry.name
}

output "iot_endpoint" {
  value = var.enable_iot ? data.aws_iot_endpoint.ats[0].endpoint_address : "IoT disabled"
}

output "dashboard_bucket" {
  value = terraform_data.dashboard_bucket.output
}

output "cloudfront_distribution_id" {
  value = var.enable_cloudfront ? aws_cloudfront_distribution.dashboard[0].id : ""
}

output "lambda_functions" {
  value = {
    processor   = aws_lambda_function.processor.function_name
    connections = aws_lambda_function.connections.function_name
    api         = aws_lambda_function.api.function_name
    site        = aws_lambda_function.site.function_name
  }
}

output "cloudwatch_dashboard" {
  value = "https://${var.region}.console.aws.amazon.com/cloudwatch/home?region=${var.region}#dashboards:name=${aws_cloudwatch_dashboard.fleet.dashboard_name}"
}
