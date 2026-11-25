###############################################################################
# Monitoring: CloudWatch alarms (sent to the same SNS topic) + a dashboard.
###############################################################################

resource "aws_cloudwatch_metric_alarm" "processor_errors" {
  alarm_name          = "${local.name}-processor-errors"
  alarm_description   = "Telemetry processor Lambda is failing"
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = aws_lambda_function.processor.function_name }
  statistic           = "Sum"
  period              = 60
  evaluation_periods  = 3
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

# Iterator age = how far behind the consumer is. High = data delayed (SLO risk).
resource "aws_cloudwatch_metric_alarm" "iterator_age" {
  alarm_name          = "${local.name}-kinesis-iterator-age"
  alarm_description   = "Telemetry is being processed late (end-to-end latency SLO at risk)"
  namespace           = "AWS/Lambda"
  metric_name         = "IteratorAge"
  dimensions          = { FunctionName = aws_lambda_function.processor.function_name }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 2
  threshold           = 60000
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "invalid_signatures" {
  alarm_name          = "${local.name}-invalid-signatures"
  alarm_description   = "Frames with bad HMAC signatures received (possible tampering)"
  namespace           = "FleetTelemetry"
  metric_name         = "InvalidSignature"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 5
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_dashboard" "fleet" {
  dashboard_name = "${local.name}-fleet-telemetry"
  dashboard_body = jsonencode({
    widgets = [
      {
        type   = "metric"
        x      = 0
        y      = 0
        width  = 12
        height = 6

        properties = {
          title   = "Kinesis incoming records"
          region  = var.region
          stat    = "Sum"
          period  = 60
          metrics = [["AWS/Kinesis", "IncomingRecords", "StreamName", aws_kinesis_stream.telemetry.name]]
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 0
        width  = 12
        height = 6

        properties = {
          title  = "Processor duration (ms) and errors"
          region = var.region
          period = 60
          metrics = [
            ["AWS/Lambda", "Duration", "FunctionName", aws_lambda_function.processor.function_name, { stat = "Average" }],
            ["AWS/Lambda", "Errors", "FunctionName", aws_lambda_function.processor.function_name, { stat = "Sum", yAxis = "right" }]
          ]
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 6
        width  = 12
        height = 6

        properties = {
          title  = "Anomalies and rejected frames"
          region = var.region
          stat   = "Sum"
          period = 60
          metrics = [
            ["FleetTelemetry", "AnomaliesDetected"],
            ["FleetTelemetry", "InvalidSignature"],
            ["FleetTelemetry", "InvalidFrame"]
          ]
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 6
        width  = 12
        height = 6

        properties = {
          title   = "Iterator age (ms) - processing delay"
          region  = var.region
          stat    = "Maximum"
          period  = 60
          metrics = [["AWS/Lambda", "IteratorAge", "FunctionName", aws_lambda_function.processor.function_name]]
        }
      }
    ]
  })
}
