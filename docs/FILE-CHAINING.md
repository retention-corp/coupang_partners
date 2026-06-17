# File Chaining

Use this file to choose the correct next document before editing code.

## Chain 1: OpenClaw / Discord Integration

1. [README.md](../README.md)
2. [docs/REMAINING-WORK.md](REMAINING-WORK.md) → `OpenClaw / Discord`
3. [AGENT-USAGE.md](../AGENT-USAGE.md)
4. [openclaw_skill/README.md](../openclaw_skill/README.md)
5. [openclaw_skill/SKILL.md](../openclaw_skill/SKILL.md)
6. [docs/AGENT-USAGE.md](AGENT-USAGE.md)
7. Relevant runtime/client code:
   - [bin/openclaw_shopping.py](../bin/openclaw_shopping.py)
   - [openclaw_skill/scripts/openclaw-shopping-skill.py](../openclaw_skill/scripts/openclaw-shopping-skill.py)
   - [scripts/agent_smoke.py](../scripts/agent_smoke.py)

## Chain 2: Stability / Scale

1. [README.md](../README.md)
2. [docs/REMAINING-WORK.md](REMAINING-WORK.md) → `Stability`
3. Core backend files:
   - [backend.py](../backend.py)
   - [analytics.py](../analytics.py)
   - [url_shortener.py](../url_shortener.py)
   - [security.py](../security.py)
   - [recommendation.py](../recommendation.py)
4. Tests:
   - [test_backend.py](../test_backend.py)
   - [test_recommendation.py](../test_recommendation.py)
   - [test_url_shortener.py](../test_url_shortener.py)
   - [test_analytics.py](../test_analytics.py)
5. Operations checks:
   - [scripts/agent_smoke.py](../scripts/agent_smoke.py)
   - [scripts/agent_closed_loop.py](../scripts/agent_closed_loop.py)
   - [scripts/closed_loop_check.py](../scripts/closed_loop_check.py)
   - [scripts/closed_loop_ops.py](../scripts/closed_loop_ops.py)
   - [docs/OPERATIONS-CLOSED-LOOP.md](OPERATIONS-CLOSED-LOOP.md)

## Chain 3: GCP Deploy / Redeploy

1. [README.md](../README.md)
2. [docs/REMAINING-WORK.md](REMAINING-WORK.md) → `Deployment`
3. Deployment files:
   - [Dockerfile](../Dockerfile)
   - [requirements.txt](../requirements.txt)
   - [scripts/deploy_gcp_cloud_run.sh](../scripts/deploy_gcp_cloud_run.sh)
   - [scripts/smoke_test_hosted_backend.py](../scripts/smoke_test_hosted_backend.py)
   - [scripts/agent_smoke.py](../scripts/agent_smoke.py)
   - [scripts/agent_closed_loop.py](../scripts/agent_closed_loop.py)
   - [scripts/closed_loop_check.py](../scripts/closed_loop_check.py)
   - [scripts/closed_loop_ops.py](../scripts/closed_loop_ops.py)
   - [scripts/run_openclaw_backend.fish](../scripts/run_openclaw_backend.fish)
4. Service/runtime:
   - [backend.py](../backend.py)
   - [security.py](../security.py)

## Chain 4: Agent Integration / Closed-Loop Ops

1. [README.md](../README.md)
2. [AGENT-USAGE.md](../AGENT-USAGE.md)
3. [docs/AGENT-USAGE.md](AGENT-USAGE.md)
4. [docs/OPERATIONS-CLOSED-LOOP.md](OPERATIONS-CLOSED-LOOP.md)
5. [docs/AGENTIC-CLOSED-LOOP.md](AGENTIC-CLOSED-LOOP.md)
6. Runtime/client files:
   - [agent_manifest.json](../agent_manifest.json)
   - [scripts/agent_smoke.py](../scripts/agent_smoke.py)
   - [scripts/agent_closed_loop.py](../scripts/agent_closed_loop.py)
   - [scripts/closed_loop_check.py](../scripts/closed_loop_check.py)
   - [scripts/closed_loop_ops.py](../scripts/closed_loop_ops.py)
   - [bin/openclaw_shopping.py](../bin/openclaw_shopping.py)
   - [coupang_mcp_client.py](../coupang_mcp_client.py)
7. Tests:
   - [test_agent_smoke.py](../test_agent_smoke.py)
   - [test_agent_closed_loop.py](../test_agent_closed_loop.py)
   - [test_closed_loop_ops.py](../test_closed_loop_ops.py)
   - [test_backend.py](../test_backend.py)

## Default Rule

If the task touches production behavior, read the relevant chain fully before
editing. If the task only updates docs, read the corresponding section in
[docs/REMAINING-WORK.md](REMAINING-WORK.md) first.
