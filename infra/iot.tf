###############################################################################
# AWS IoT Core: device identity (X.509), least-privilege policy, and a rule
# that forwards every MQTT message on fleet/telemetry/<vehicleId> to Kinesis.
# Turn off with enable_iot = false if your lab account blocks IoT.
###############################################################################

data "aws_iot_endpoint" "ats" {
  count         = var.enable_iot ? 1 : 0
  endpoint_type = "iot:Data-ATS"
}

resource "aws_iot_thing" "simulator" {
  count = var.enable_iot ? 1 : 0
  name  = "${local.name}-fleet-simulator"
}

resource "aws_iot_certificate" "simulator" {
  count  = var.enable_iot ? 1 : 0
  active = true
}

resource "aws_iot_thing_principal_attachment" "simulator" {
  count     = var.enable_iot ? 1 : 0
  thing     = aws_iot_thing.simulator[0].name
  principal = aws_iot_certificate.simulator[0].arn
}

# Least privilege: this certificate may only connect with its own client id
# and only publish to the fleet telemetry topics.
resource "aws_iot_policy" "simulator" {
  count = var.enable_iot ? 1 : 0
  name  = "${local.name}-fleet-device-policy"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = "iot:Connect"
        Resource = "arn:aws:iot:${var.region}:${data.aws_caller_identity.current.account_id}:client/${local.name}-fleet-simulator"
      },
      {
        Effect   = "Allow"
        Action   = "iot:Publish"
        Resource = "arn:aws:iot:${var.region}:${data.aws_caller_identity.current.account_id}:topic/fleet/telemetry/*"
      }
    ]
  })
}

resource "aws_iot_policy_attachment" "simulator" {
  count  = var.enable_iot ? 1 : 0
  policy = aws_iot_policy.simulator[0].name
  target = aws_iot_certificate.simulator[0].arn
}

resource "aws_iot_topic_rule" "to_kinesis" {
  count       = var.enable_iot ? 1 : 0
  name        = "${local.name}_telemetry_to_kinesis"
  description = "Forward fleet telemetry from MQTT to Kinesis"
  enabled     = true
  sql         = "SELECT * FROM 'fleet/telemetry/+'"
  sql_version = "2016-03-23"

  kinesis {
    role_arn      = data.aws_iam_role.lab.arn
    stream_name   = aws_kinesis_stream.telemetry.name
    partition_key = "$${topic(3)}" # the vehicleId part of the topic
  }
}

# Amazon root CA used by the simulator to trust IoT Core's TLS certificate
data "http" "amazon_root_ca" {
  count = var.enable_iot ? 1 : 0
  url   = "https://www.amazontrust.com/repository/AmazonRootCA1.pem"
}

# Write certificate files for the simulator (git-ignored, keep them private!)
resource "local_sensitive_file" "device_cert" {
  count           = var.enable_iot ? 1 : 0
  content         = aws_iot_certificate.simulator[0].certificate_pem
  filename        = "${path.module}/../simulator/generated/certs/device.pem.crt"
  file_permission = "0600"
}

resource "local_sensitive_file" "device_key" {
  count           = var.enable_iot ? 1 : 0
  content         = aws_iot_certificate.simulator[0].private_key
  filename        = "${path.module}/../simulator/generated/certs/private.pem.key"
  file_permission = "0600"
}

resource "local_file" "root_ca" {
  count    = var.enable_iot ? 1 : 0
  content  = data.http.amazon_root_ca[0].response_body
  filename = "${path.module}/../simulator/generated/certs/AmazonRootCA1.pem"
}
