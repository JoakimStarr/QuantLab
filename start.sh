#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

MODE="${1:-dev}"

if [ "$MODE" = "dev" ]; then
    echo "========================================="
    echo "  QuantLab - 量化策略回测研究平台"
    echo "========================================="
    echo ""

    echo "[1/2] Starting backend (port 8000)..."
    cd backend
    uvicorn app.main:app --reload --host 0.0.0.0 --port 8000 &
    BACKEND_PID=$!

    echo "[2/2] Starting frontend (port 3000)..."
    cd "$SCRIPT_DIR/frontend"
    npm run dev &
    FRONTEND_PID=$!

    echo ""
    echo "========================================="
    echo "  Backend:  http://localhost:8000"
    echo "  API Docs: http://localhost:8000/docs"
    echo "  Frontend: http://localhost:3000"
    echo "========================================="
    echo "Press Ctrl+C to stop all services"

    trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit" SIGINT SIGTERM
    wait
elif [ "$MODE" = "docker" ]; then
    echo "Starting in docker mode..."
    docker-compose up --build
else
    echo "Usage: ./start.sh [dev|docker]"
    exit 1
fi