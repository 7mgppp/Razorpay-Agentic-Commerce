import uvicorn
import os

if __name__ == "__main__":
    # Ensure frontend directories exist
    frontend_dir = os.path.join(os.path.dirname(__file__), "frontend")
    os.makedirs(frontend_dir, exist_ok=True)
    
    print("Starting Mandate Layer — Agentic Commerce Safety Layer server...")
    print("Web UI will be accessible at: http://localhost:8000")
    print("Websocket live stream at: ws://localhost:8000/ws/live")
    print("Press Ctrl+C to terminate the server.\n")
    
    # Run uvicorn server
    uvicorn.run("backend.app:app", host="0.0.0.0", port=8000, reload=True)
