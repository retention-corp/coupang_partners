# File Chaining

Use this file to choose the correct next document before editing code.

## Chain 1: OpenClaw / Discord Integration

1. [README.md](../README.md)
2. [docs/REMAINING-WORK.md](REMAINING-WORK.md) → `OpenClaw / Discord`
3. [openclaw_skill/README.md](../openclaw_skill/README.md)
4. [openclaw_skill/SKILL.md](../openclaw_skill/SKILL.md)
5. Relevant runtime/client code:
   - [bin/openclaw_shopping.py](../bin/openclaw_shopping.py)
   - [openclaw_skill/scripts/openclaw-shopping-skill.py](../openclaw_skill/scripts/openclaw-shopping-skill.py)

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

## Chain 3: GCP Deploy / Redeploy

1. [README.md](../README.md)
2. [docs/REMAINING-WORK.md](REMAINING-WORK.md) → `Deployment`
3. Deployment files:
   - [Dockerfile](../Dockerfile)
   - [requirements.txt](../requirements.txt)
   - [scripts/deploy_gcp_cloud_run.sh](../scripts/deploy_gcp_cloud_run.sh)
   - [scripts/run_openclaw_backend.fish](../scripts/run_openclaw_backend.fish)
4. Service/runtime:
   - [backend.py](../backend.py)
   - [security.py](../security.py)

## Default Rule

If the task touches production behavior, read the relevant chain fully before
editing. If the task only updates docs, read the corresponding section in
[docs/REMAINING-WORK.md](REMAINING-WORK.md) first.
