variable "region" {
  description = "AWS region. Learner Lab works in us-east-1 (and us-west-2)."
  type        = string
  default     = "us-east-1"
}

variable "project" {
  description = "Short name used as a prefix for every resource."
  type        = string
  default     = "fleet"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,15}$", var.project))
    error_message = "Use 2-16 lowercase letters, digits or dashes."
  }
}

variable "alert_email" {
  description = "Email address that receives SNS alerts (you must click the confirmation link)."
  type        = string
  default     = ""
}

variable "lab_role_name" {
  description = "Learner Lab does not allow creating IAM roles. We reuse the pre-made LabRole."
  type        = string
  default     = "LabRole"
}

variable "enable_iot" {
  description = "Create AWS IoT Core thing, X.509 certificate, policy and rule (IoT -> Kinesis)."
  type        = bool
  default     = true
}

variable "enable_cloudfront" {
  description = "Serve the dashboard over HTTPS through CloudFront."
  type        = bool
  default     = true
}

variable "cargo_temp_max" {
  description = "Cold-chain upper temperature limit in deg C."
  type        = number
  default     = 8
}

variable "speed_max" {
  description = "Speed limit in km/h."
  type        = number
  default     = 90
}

variable "retention_days" {
  description = "How many days telemetry is kept in DynamoDB (TTL)."
  type        = number
  default     = 7
}

variable "kinesis_retention_hours" {
  description = "Kinesis replay window (24 h is the included minimum)."
  type        = number
  default     = 24
}
