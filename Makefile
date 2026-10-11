# Pass env.json to `sam local invoke` when it exists (copy env.example.json to start)
ENV_VARS := $(if $(wildcard env.json),--env-vars env.json,)

.PHONY: help install build deploy deploy-guided test test-coverage clean validate lint dashboard dashboard-live

help: ## Show this help message
	@echo 'Usage: make [target]'
	@echo ''
	@echo 'Available targets:'
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  %-15s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install: ## Install Python dependencies
	pip install -r requirements-dev.txt

build: ## Build the SAM application
	sam build

deploy: ## Deploy the SAM application
	sam deploy

deploy-guided: ## Deploy with guided prompts (first time)
	sam deploy --guided

test: ## Run tests
	pytest tests/ -v

test-coverage: ## Run tests with coverage (requires pytest-cov)
	pytest tests/ -v --cov=src --cov-report=html

clean: ## Clean build artifacts
	rm -rf .aws-sam/
	rm -rf build/
	find . -type d -name __pycache__ -exec rm -r {} +
	find . -type f -name "*.pyc" -delete

invoke-reddit-fetcher: ## Invoke the Reddit fetcher function locally
	sam local invoke RedditFetcherFunction -e events/reddit-fetcher-event.json $(ENV_VARS)

invoke-analyzer: ## Invoke the analyzer function locally
	sam local invoke AnalyzerFunction -e events/analyzer-event.json $(ENV_VARS)

invoke-summarizer: ## Invoke the summarizer function locally
	sam local invoke SummarizerFunction -e events/summarizer-event.json $(ENV_VARS)

invoke-emailer: ## Invoke the emailer function locally
	sam local invoke EmailerFunction -e events/emailer-event.json $(ENV_VARS)

invoke-reddit-fetcher-remote: ## Invoke the deployed Reddit fetcher function remotely (requires AWS CLI)
	@./scripts/invoke-lambda.sh RedditFetcherFunction reddit-fetcher

invoke-analyzer-remote: ## Invoke the deployed analyzer function remotely (requires AWS CLI)
	@./scripts/invoke-lambda.sh AnalyzerFunction analyzer

invoke-summarizer-remote: ## Invoke the deployed summarizer function remotely (requires AWS CLI)
	@./scripts/invoke-lambda.sh SummarizerFunction summarizer

invoke-emailer-remote: ## Invoke the deployed emailer function remotely (requires AWS CLI)
	@./scripts/invoke-lambda.sh EmailerFunction emailer

dashboard: ## Run the dashboard locally with sample data (http://127.0.0.1:8000)
	python scripts/dev_server.py

dashboard-live: ## Run the dashboard locally against the deployed bucket
	python scripts/dev_server.py --bucket $$(aws cloudformation describe-stacks --stack-name ubc-reddit-reporter \
		--query 'Stacks[0].Outputs[?OutputKey==`DataBucketName`].OutputValue' --output text)

validate: ## Validate and lint the SAM template
	sam validate --lint

lint: ## Lint Python code
	flake8 src/ tests/ scripts/ --count --select=E9,F63,F7,F82 --show-source --statistics
	flake8 src/ tests/ scripts/ --count --exit-zero --max-complexity=10 --max-line-length=127 --statistics

