"""Railway cron entry point for persistent Klaviyo delivery retries."""
from src.services.klaviyo_delivery_service import run_retry_job


if __name__ == "__main__":
    run_retry_job()
