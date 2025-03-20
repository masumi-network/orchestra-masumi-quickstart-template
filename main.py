import os
import uvicorn
import uuid
from dotenv import load_dotenv
from fastapi import FastAPI, Query, HTTPException
from pydantic import BaseModel
from masumi.config import Config
from masumi.payment import Payment, Amount
from crew_definition import ResearchCrew
from logging_config import setup_logging
from logging import Logger

# Configure logging
logger = setup_logging()

# Load environment variables
load_dotenv(override=True)

# Retrieve API Keys and URLs
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
PAYMENT_SERVICE_URL = os.getenv("PAYMENT_SERVICE_URL")
PAYMENT_API_KEY = os.getenv("PAYMENT_API_KEY")

logger.info("Starting application with configuration:")
logger.info(f"PAYMENT_SERVICE_URL: {PAYMENT_SERVICE_URL}")

# Initialize FastAPI
app = FastAPI()

# ─────────────────────────────────────────────────────────────────────────────
# Temporary in-memory job store (DO NOT USE IN PRODUCTION)
# ─────────────────────────────────────────────────────────────────────────────
jobs = {}
payment_instances = {}

# ─────────────────────────────────────────────────────────────────────────────
# Initialize Masumi Payment Config
# ─────────────────────────────────────────────────────────────────────────────
config = Config(
    payment_service_url=PAYMENT_SERVICE_URL,
    payment_api_key=PAYMENT_API_KEY
)

# ─────────────────────────────────────────────────────────────────────────────
# Pydantic Models
# ─────────────────────────────────────────────────────────────────────────────
class StartJobRequest(BaseModel):
    text: str

class ProvideInputRequest(BaseModel):
    job_id: str

# ─────────────────────────────────────────────────────────────────────────────
# Orchestra Task Execution
# ─────────────────────────────────────────────────────────────────────────────
async def execute_orchestra_task(input_data: str) -> str:
    """ Execute a Orchestra task with Research and Writing Agents """
    logger.info(f"Starting Orchestra task with input: {input_data}")
    
    # Create a crew instance
    crew = ResearchCrew(logger)
    
    # Create and execute the task
    task = crew.create_task(input_data)
    result = await task
    
    logger.info("Orchestra task completed successfully")
    return result

# ─────────────────────────────────────────────────────────────────────────────
# 1) Start Job (MIP-003: /start_job)
# ─────────────────────────────────────────────────────────────────────────────
@app.post("/start_job")
async def start_job(data: StartJobRequest):
    """ Initiates a job and creates a payment request """
    job_id = str(uuid.uuid4())
    agent_identifier = os.getenv("AGENT_IDENTIFIER")
    
    # Log the input text (truncate if too long)
    input_text = data.text
    truncated_input = input_text[:100] + "..." if len(input_text) > 100 else input_text
    logger.info(f"Received job request with input: '{truncated_input}'")
    logger.info(f"Starting job {job_id} with agent {agent_identifier}")

    # Define payment amounts
    payment_amount = os.getenv("PAYMENT_AMOUNT", "10000000")  # Default 10 ADA
    payment_unit = os.getenv("PAYMENT_UNIT", "lovelace") # Default lovelace
    amounts = [Amount(amount=payment_amount, unit=payment_unit)]
    logger.info(f"Using payment amount: {payment_amount} {payment_unit}")
    
    # Create a payment request using Masumi
    payment = Payment(
        agent_identifier=agent_identifier,
        amounts=amounts,
        config=config,
        identifier_from_purchaser="default_purchaser_id", # Best practice: Replace with a random identifier for each purchase
        input_data=data.text
    )
    
    logger.info("Creating payment request...")
    payment_request = await payment.create_payment_request()
    payment_id = payment_request["data"]["blockchainIdentifier"]
    payment.payment_ids.add(payment_id)
    logger.info(f"Created payment request with ID: {payment_id}")

    # Store job info (Awaiting payment)
    jobs[job_id] = {
        "status": "awaiting_payment",
        "payment_status": "pending",
        "payment_id": payment_id,
        "input_data": data.text,
        "result": None
    }

    async def payment_callback(payment_id: str):
        await handle_payment_status(job_id, payment_id)

    # Start monitoring the payment status
    payment_instances[job_id] = payment
    logger.info(f"Starting payment status monitoring for job {job_id}")
    await payment.start_status_monitoring(payment_callback)

    # Return the response in the required format
    return {
        "status": "success",
        "job_id": job_id,
        "blockchainIdentifier": payment_request["data"]["blockchainIdentifier"],
        "submitResultTime": payment_request["data"]["submitResultTime"],
        "unlockTime": payment_request["data"]["unlockTime"],
        "externalDisputeUnlockTime": payment_request["data"]["externalDisputeUnlockTime"],
        "agentIdentifier": agent_identifier,
        "sellerVkey": os.getenv("SELLER_VKEY"),
        "identifierFromPurchaser": payment.identifier_from_purchaser,
        "amounts": amounts,
        "input_hash": payment.input_hash
    }

# ─────────────────────────────────────────────────────────────────────────────
# 2) Process Payment and Execute AI Task
# ─────────────────────────────────────────────────────────────────────────────
async def handle_payment_status(job_id: str, payment_id: str) -> None:
    """ Executes Orchestra task after payment confirmation """
    try:
        logger.info(f"Payment {payment_id} completed for job {job_id}, executing task...")
        
        # Update job status to running
        jobs[job_id]["status"] = "running"
        logger.info(f"Input data: {jobs[job_id]["input_data"]}")

        # Execute the AI task
        result = await execute_orchestra_task(jobs[job_id]["input_data"])
        logger.info(f"Crew task completed for job {job_id}")

        # Convert result to string if it's not already
        result_str = str(result)
        
        # Mark payment as completed on Masumi
        # Use a shorter string for the result hash
        result_hash = result_str[:64] if len(result_str) >= 64 else result_str
        await payment_instances[job_id].complete_payment(payment_id, result_hash)
        logger.info(f"Payment completed for job {job_id}")

        # Update job status
        jobs[job_id]["status"] = "completed"
        jobs[job_id]["payment_status"] = "completed"
        jobs[job_id]["result"] = result

        # Stop monitoring payment status
        if job_id in payment_instances:
            payment_instances[job_id].stop_status_monitoring()
            del payment_instances[job_id]
    except Exception as e:
        logger.error(f"Error processing payment {payment_id} for job {job_id}: {str(e)}", exc_info=True)
        jobs[job_id]["status"] = "failed"
        jobs[job_id]["error"] = str(e)
        
        # Still stop monitoring to prevent repeated failures
        if job_id in payment_instances:
            payment_instances[job_id].stop_status_monitoring()
            del payment_instances[job_id]

# ─────────────────────────────────────────────────────────────────────────────
# 3) Check Job and Payment Status (MIP-003: /status)
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/status")
async def get_status(job_id: str):
    """ Retrieves the current status of a specific job """
    logger.info(f"Checking status for job {job_id}")
    if job_id not in jobs:
        logger.warning(f"Job {job_id} not found")
        raise HTTPException(status_code=404, detail="Job not found")

    job = jobs[job_id]

    # Check latest payment status if payment instance exists
    if job_id in payment_instances:
        try:
            status = await payment_instances[job_id].check_payment_status()
            job["payment_status"] = status.get("data", {}).get("status")
            logger.info(f"Updated payment status for job {job_id}: {job['payment_status']}")
        except ValueError as e:
            logger.warning(f"Error checking payment status: {str(e)}")
            job["payment_status"] = "unknown"
        except Exception as e:
            logger.error(f"Error checking payment status: {str(e)}", exc_info=True)
            job["payment_status"] = "error"

    return {
        "job_id": job_id,
        "status": job["status"],
        "payment_status": job["payment_status"],
        "result": job.get("result")
    }

# ─────────────────────────────────────────────────────────────────────────────
# 4) Check Server Availability (MIP-003: /availability)
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/availability")
async def check_availability():
    """ Checks if the server is operational """
    return {
        "status": "available",
        "message": "The server is running smoothly."
    }

# ─────────────────────────────────────────────────────────────────────────────
# 5) Retrieve Input Schema (MIP-003: /input_schema)
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/input_schema")
async def input_schema():
    """
    Returns the expected input schema for the /start_job endpoint.
    Fulfills MIP-003 /input_schema endpoint.
    """
    # Example response defining the accepted key-value pairs
    schema_example = {
        "input_data": [
            {"key": "text", "value": "string"}
        ]
    }
    return schema_example

# ─────────────────────────────────────────────────────────────────────────────
# 6) Health Check
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    """
    Returns the health of the server.
    """
    return {
        "status": "healthy"
    }

# ─────────────────────────────────────────────────────────────────────────────
# Main Logic if Called as a Script
# ─────────────────────────────────────────────────────────────────────────────
def main():
    print("Running Orchestra as standalone script is not supported when using payments.")
    print("Start the API using `python main.py api` instead.")

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "api":
        print("Starting FastAPI server with Masumi integration...")
        uvicorn.run(app, host="0.0.0.0", port=8000)
    else:
        main()
