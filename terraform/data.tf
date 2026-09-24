resource "random_password" "db" {
  length  = 32
  special = false # lives inside DATABASE_URL
}

resource "random_password" "redis" {
  length  = 32
  special = false
}

resource "aws_db_subnet_group" "main" {
  name       = var.project
  subnet_ids = aws_subnet.private[*].id
}

resource "aws_db_instance" "main" {
  identifier                 = var.project
  engine                     = "postgres"
  engine_version             = "16"
  instance_class             = var.db_instance_class
  allocated_storage          = var.db_allocated_storage
  storage_type               = "gp3"
  storage_encrypted          = true
  db_name                    = var.project
  username                   = var.project
  password                   = random_password.db.result
  db_subnet_group_name       = aws_db_subnet_group.main.name
  vpc_security_group_ids     = [aws_security_group.db.id]
  publicly_accessible        = false
  backup_retention_period    = 7
  auto_minor_version_upgrade = true
  deletion_protection        = var.db_deletion_protection
  skip_final_snapshot        = false
  final_snapshot_identifier  = "${var.project}-final"
}

resource "aws_elasticache_subnet_group" "main" {
  name       = var.project
  subnet_ids = aws_subnet.private[*].id
}

# no persistence needed, every key is a bucket or an idempotency record with a TTL
resource "aws_elasticache_replication_group" "main" {
  replication_group_id       = var.project
  description                = "${var.project} rate limiting and idempotency"
  engine                     = "redis"
  engine_version             = "7.1"
  node_type                  = var.redis_node_type
  num_cache_clusters         = 1
  port                       = 6379
  subnet_group_name          = aws_elasticache_subnet_group.main.name
  security_group_ids         = [aws_security_group.redis.id]
  at_rest_encryption_enabled = true
  transit_encryption_enabled = true
  auth_token                 = random_password.redis.result
  parameter_group_name       = "default.redis7"
}
