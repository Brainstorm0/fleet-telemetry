# Writes simulator/generated/config.json so the simulator knows where to send
# data and which secret to sign with. This file is git-ignored.
resource "local_sensitive_file" "simulator_config" {
  filename        = "${path.module}/../simulator/generated/config.json"
  file_permission = "0600"

  content = jsonencode({
    region       = var.region
    stream_name  = aws_kinesis_stream.telemetry.name
    iot_endpoint = var.enable_iot ? data.aws_iot_endpoint.ats[0].endpoint_address : ""
    topic_prefix = "fleet/telemetry"
    client_id    = "${local.name}-fleet-simulator"
    hmac_secret  = random_password.hmac.result
  })
}
