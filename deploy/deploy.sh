#!/usr/bin/env bash
# Deploys an image already in ECR to the app stack (deploy/aws/app.yaml):
#
#   AWS_REGION=us-east-1 deploy/deploy.sh <commit sha>
#
# 1. moves only the migration task definition to the new image
# 2. runs `alembic upgrade head` as a one-off task and requires exit code 0
# 3. moves the service to the new image. If the new tasks never get healthy,
#    the circuit breaker rolls the service back and CloudFormation rolls the
#    stack back with it, so this step fails with the previous release live.
#
# Migrations are never rolled back: keep them backward compatible (add in one
# release, drop in a later one), the old image keeps serving during step 3.
#
# The first run creates the stack without the service, migrates the empty
# database, then creates the service. Optional env: STACK_NAME (relayix),
# BOOTSTRAP_STACK_NAME (relayix-bootstrap), CERTIFICATE_ARN.
set -euo pipefail

SHA="${1:?usage: deploy/deploy.sh <image tag, the commit sha CI pushed>}"
STACK="${STACK_NAME:-relayix}"
BOOTSTRAP="${BOOTSTRAP_STACK_NAME:-relayix-bootstrap}"
TEMPLATE="$(dirname "$0")/aws/app.yaml"

cert_override=()
if [[ -n "${CERTIFICATE_ARN:-}" ]]; then
    cert_override=("CertificateArn=${CERTIFICATE_ARN}")
fi

# parameters left out keep the stack's current value
cfn_deploy() {
    echo "==> stack update: $*"
    aws cloudformation deploy \
        --stack-name "${STACK}" \
        --template-file "${TEMPLATE}" \
        --capabilities CAPABILITY_IAM \
        --no-fail-on-empty-changeset \
        --parameter-overrides "BootstrapStackName=${BOOTSTRAP}" "${cert_override[@]}" "$@"
}

stack_output() {
    aws cloudformation describe-stacks --stack-name "$1" \
        --query "Stacks[0].Outputs[?OutputKey=='$2'].OutputValue" --output text
}

stack_status() {
    aws cloudformation describe-stacks --stack-name "${STACK}" \
        --query "Stacks[0].StackStatus" --output text 2>/dev/null || echo MISSING
}

run_migrations() {
    local cluster capacity_provider task_def run task exit_code
    cluster="$(stack_output "${STACK}" ClusterName)"
    capacity_provider="$(stack_output "${STACK}" CapacityProviderName)"
    task_def="$(stack_output "${STACK}" MigrateTaskDefinitionArn)"

    echo "==> running migrations (${task_def##*/})"
    run="$(aws ecs run-task \
        --cluster "${cluster}" \
        --task-definition "${task_def}" \
        --capacity-provider-strategy "capacityProvider=${capacity_provider},weight=1" \
        --started-by "deploy-${SHA:0:20}" \
        --output json)"
    task="$(jq -r '.tasks[0].taskArn // empty' <<<"${run}")"
    if [[ -z "${task}" ]]; then
        echo "::error::migration task did not start: $(jq -c '.failures' <<<"${run}")"
        exit 1
    fi

    # 10 minutes, room for the capacity provider to add an instance
    aws ecs wait tasks-stopped --cluster "${cluster}" --tasks "${task}"

    exit_code="$(aws ecs describe-tasks --cluster "${cluster}" --tasks "${task}" \
        --query "tasks[0].containers[?name=='migrate'].exitCode | [0]" --output text)"
    if [[ "${exit_code}" != "0" ]]; then
        echo "::error::migrations failed (exit ${exit_code}), see the /ecs/${STACK}/migrate log group"
        aws ecs describe-tasks --cluster "${cluster}" --tasks "${task}" \
            --query "tasks[0].{stopped: stoppedReason, container: containers[0].reason}" --output json
        exit 1
    fi
    echo "==> migrations applied"
}

repo_uri="$(stack_output "${BOOTSTRAP}" RepositoryUri)"
if ! aws ecr describe-images --repository-name "${repo_uri##*/}" --image-ids "imageTag=${SHA}" >/dev/null 2>&1; then
    echo "::error::${repo_uri}:${SHA} not found, has CI pushed it?"
    exit 1
fi

status="$(stack_status)"
case "${status}" in
    MISSING)
        cfn_deploy "ImageTag=${SHA}" "MigrateImageTag=" "RunService=false"
        run_migrations
        cfn_deploy "RunService=true"
        ;;
    ROLLBACK_COMPLETE)
        echo "::error::stack ${STACK} failed its first create, delete it and run again"
        exit 1
        ;;
    *_IN_PROGRESS)
        echo "::error::stack ${STACK} is ${status}, wait for it to settle"
        exit 1
        ;;
    *)
        cfn_deploy "MigrateImageTag=${SHA}"
        run_migrations
        cfn_deploy "ImageTag=${SHA}" "MigrateImageTag=" "RunService=true"
        ;;
esac

echo "==> ${SHA} is live at http://$(stack_output "${STACK}" AlbDnsName)"
