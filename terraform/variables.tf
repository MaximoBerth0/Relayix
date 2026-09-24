variable "project" {
  type    = string
  default = "relayix"
}

variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "image_tag" {
  description = "ECR tag to run, the commit sha CI pushed"
  type        = string
}

variable "certificate_arn" {
  description = "ACM certificate for the API domain, same region as the ALB"
  type        = string
}

variable "github_repo" {
  description = "owner/repo allowed to push to ECR through OIDC"
  type        = string
  default     = "MaximoBerth0/Relayix"
}

variable "create_github_oidc_provider" {
  description = "false if the account already has the GitHub OIDC provider"
  type        = bool
  default     = true
}

# ECS

# 1 vCPU / 2GB for 4 uvicorn workers
variable "task_cpu" {
  type    = number
  default = 1024
}

variable "task_memory" {
  type    = number
  default = 2048
}

variable "desired_count" {
  type    = number
  default = 1
}

variable "max_count" {
  type    = number
  default = 3
}

variable "adot_image" {
  description = "AWS Distro for OpenTelemetry collector, pin a version in prod"
  type        = string
  default     = "public.ecr.aws/aws-observability/aws-otel-collector:latest"
}

variable "log_retention_days" {
  type    = number
  default = 14
}

# data stores

variable "db_instance_class" {
  type    = string
  default = "db.t4g.micro"
}

variable "db_allocated_storage" {
  type    = number
  default = 20
}

variable "db_deletion_protection" {
  type    = bool
  default = true
}

variable "redis_node_type" {
  type    = string
  default = "cache.t4g.micro"
}

# app settings, see app/infra/config.py

variable "app_env" {
  description = "extra plain env vars for the api container"
  type        = map(string)
  default     = {}
}
