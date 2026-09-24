output "alb_dns_name" {
  description = "point the API domain (CNAME/alias) here"
  value       = aws_lb.main.dns_name
}

output "ecr_repository_url" {
  value = aws_ecr_repository.api.repository_url
}

output "github_actions_role_arn" {
  description = "set as the AWS_ROLE_ARN repository variable in GitHub"
  value       = aws_iam_role.github_actions.arn
}

output "admin_api_token_secret" {
  description = "ADMIN_API_TOKEN lives in this secret"
  value       = aws_secretsmanager_secret.app.name
}

output "migrate_command" {
  description = "run after every apply that ships a new migration"
  value = join(" ", [
    "aws ecs run-task --region ${var.aws_region}",
    "--cluster ${aws_ecs_cluster.main.name}",
    "--task-definition ${aws_ecs_task_definition.api.arn}",
    "--launch-type FARGATE",
    "--network-configuration 'awsvpcConfiguration={subnets=[${join(",", aws_subnet.public[*].id)}],securityGroups=[${aws_security_group.app.id}],assignPublicIp=ENABLED}'",
    "--overrides '{\"containerOverrides\":[{\"name\":\"api\",\"command\":[\"alembic\",\"upgrade\",\"head\"]}]}'",
  ])
}
