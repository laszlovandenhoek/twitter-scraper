import json
from fastapi import FastAPI, Request
import logging
from fastapi.responses import JSONResponse
import uvicorn

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = FastAPI()

@app.middleware("http")
async def log_requests(request: Request, call_next):
    # Log the request details
    logger.info(f"Request: {request.method} {request.url}")
    logger.info(f"Headers: {request.headers}")
    
    # Get the request body if any
    body = await request.body()
    if body:
        logger.info(f"Body: {body.decode()}")
    
    # Process the request
    response = await call_next(request)
    
    # Log the response status
    logger.info(f"Response status: {response.status_code}")
    
    return response

def respond_using_file(file_name: str) -> JSONResponse:
    with open(file_name, "r") as f:
        serialized = json.load(f)
        
        headers = serialized["raw"]["response"]["headers"]
        
        escaped_json = serialized["raw"]["response"]["raw_data"]
        unescaped_json = json.loads(escaped_json)
        return JSONResponse(content=unescaped_json, headers=headers)

@app.get("/graphql/2neUNDqrrFzbLui8yallcQ/Bookmarks")
async def bookmarks() -> JSONResponse:
    return respond_using_file("bookmarks.json")

@app.get("/graphql/lIDpu_NWL7_VhimGGt0o6A/Likes")
async def likes(request: Request) -> JSONResponse:
    if ("cursor" in request.query_params.get("variables")):
        print("cursor in request")
        return respond_using_file("likes2.json")
    else:
        print("no cursor in request")
        return respond_using_file("likes.json")

@app.get("/graphql/xd_EMdYvB9hfZsZ6Idri0w/TweetDetail")
async def tweet_detail() -> JSONResponse:
    return respond_using_file("tweet_details.json")

@app.get("/")
async def root():
    return {"message": "Twitter mock server is running"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000) 