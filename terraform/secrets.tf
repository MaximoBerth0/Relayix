resource "random_password" "admin_token" {
  length  = 48
  special = false
}

resource "aws_secretsmanager_secret" "app" {
  name                    = "${var.project}/app"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "app" {
  secret_id = aws_secretsmanager_secret.app.id
  secret_string = jsonencode({
    DATABASE_URL    = "postgresql+asyncpg://${aws_db_instance.main.username}:${random_password.db.result}@${aws_db_instance.main.address}:5432/${aws_db_instance.main.db_name}"
    REDIS_URL       = "rediss://:${random_password.redis.result}@${aws_elasticache_replication_group.main.primary_endpoint_address}:6379/0"
    ADMIN_API_TOKEN = random_password.admin_token.result
  })
}

# provider keys stay out of state, set them once with:
#   aws ssm put-parameter --overwrite --type SecureString --name /relayix/OPENAI_API_KEY --value sk-...
resource "aws_ssm_parameter" "provider_keys" {
  for_each = toset(["OPENAI_API_KEY", "ANTHROPIC_API_KEY"])

  name  = "/${var.project}/${each.key}"
  type  = "SecureString"
  value = "unset"

  lifecycle {
    ignore_changes = [value]
  }
}
