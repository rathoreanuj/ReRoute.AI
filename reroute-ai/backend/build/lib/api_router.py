from fastapi import APIRouter

from routers import (
    agent_router,
    auth_google_router,
    chat_router,
    disruption_router,
    health_router,
    monitor_router,
    trip_router,
    user_router,
    public_router,
    ws_router,
)

api_router = APIRouter()
api_router.include_router(health_router.router, tags=["health"])
api_router.include_router(auth_google_router.router)
api_router.include_router(agent_router.router, tags=["agent"])
api_router.include_router(chat_router.router, tags=["chat"])
api_router.include_router(trip_router.router, tags=["trips"])
api_router.include_router(user_router.router, tags=["users"])
api_router.include_router(disruption_router.router, tags=["disruptions"])
api_router.include_router(monitor_router.router, tags=["monitor"])
api_router.include_router(public_router.router)
api_router.include_router(ws_router.router)
