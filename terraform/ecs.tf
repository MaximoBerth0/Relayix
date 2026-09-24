resource "aws_ecs_cluster" "main" {
  name = var.project
}

resource "aws_cloudwatch_log_group" "api" {
  name              = "/ecs/${var.project}/api"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "otel" {
  name              = "/ecs/${var.project}/otel-collector"
  retention_in_days = var.log_retention_days
}

locals {
  # DB pool is per worker: 4 x (3 + 2) = 20 connections per task, 60 at max_count=3
  app_env = merge({
    ENVIRONMENT                 = "production"
    DEBUG                       = "false"
    DB_SSL                      = "require"
    RUN_MIGRATIONS              = "false"
    WEB_CONCURRENCY             = "4"
    DB_POOL_SIZE                = "3"
    DB_MAX_OVERFLOW             = "2"
    OTEL_ENABLED                = "true"
    OTEL_SERVICE_NAME           = var.project
    OTEL_EXPORTER_OTLP_ENDPOINT = "http://localhost:4317"
  }, var.app_env)

  app_secrets = concat(
    [for k in ["DATABASE_URL", "REDIS_URL", "ADMIN_API_TOKEN"] : {
      name      = k
      valueFrom = "${aws_secretsmanager_secret.app.arn}:${k}::"
    }],
    [for k, p in aws_ssm_parameter.provider_keys : {
      name      = k
      valueFrom = p.arn
    }],
  )

  log_config = {
    for name, group in {
      api  = aws_cloudwatch_log_group.api.name
      otel = aws_cloudwatch_log_group.otel.name
      } : name => {
      logDriver = "awslogs"
      options = {
        awslogs-group         = group
        awslogs-region        = var.aws_region
        awslogs-stream-prefix = name
      }
    }
  }
}

resource "aws_ecs_task_definition" "api" {
  family                   = "${var.project}-api"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.task_cpu
  memory                   = var.task_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }

  container_definitions = jsonencode([
    {
      name             = "api"
      image            = "${aws_ecr_repository.api.repository_url}:${var.image_tag}"
      essential        = true
      portMappings     = [{ containerPort = 8000, protocol = "tcp" }]
      environment      = [for k, v in local.app_env : { name = k, value = v }]
      secrets          = local.app_secrets
      stopTimeout      = 30
      dependsOn        = [{ containerName = "otel-collector", condition = "START" }]
      logConfiguration = local.log_config.api
    },
    {
      # receives OTLP on :4317 and ships traces to X-Ray
      name             = "otel-collector"
      image            = var.adot_image
      essential        = true
      command          = ["--config=/etc/ecs/ecs-default-config.yaml"]
      logConfiguration = local.log_config.otel
    },
  ])
}

resource "aws_ecs_service" "api" {
  name                              = "${var.project}-api"
  cluster                           = aws_ecs_cluster.main.id
  task_definition                   = aws_ecs_task_definition.api.arn
  desired_count                     = var.desired_count
  launch_type                       = "FARGATE"
  health_check_grace_period_seconds = 30

  network_configuration {
    subnets          = aws_subnet.public[*].id
    security_groups  = [aws_security_group.app.id]
    assign_public_ip = true
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.api.arn
    container_name   = "api"
    container_port   = 8000
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  # autoscaling owns the count after the first apply
  lifecycle {
    ignore_changes = [desired_count]
  }

  depends_on = [aws_lb_listener.https]
}

resource "aws_appautoscaling_target" "api" {
  service_namespace  = "ecs"
  resource_id        = "service/${aws_ecs_cluster.main.name}/${aws_ecs_service.api.name}"
  scalable_dimension = "ecs:service:DesiredCount"
  min_capacity       = var.desired_count
  max_capacity       = var.max_count
}

resource "aws_appautoscaling_policy" "api_cpu" {
  name               = "${var.project}-api-cpu"
  service_namespace  = aws_appautoscaling_target.api.service_namespace
  resource_id        = aws_appautoscaling_target.api.resource_id
  scalable_dimension = aws_appautoscaling_target.api.scalable_dimension
  policy_type        = "TargetTrackingScaling"

  target_tracking_scaling_policy_configuration {
    target_value = 60
    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageCPUUtilization"
    }
  }
}
