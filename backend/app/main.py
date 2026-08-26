"""FastAPI 应用入口"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import auth, users, tables, lineage, databases

app = FastAPI(
    title="SQLineage",
    description="SQL 血缘管理系统",
    version="0.1.0",
)

# CORS — 开发环境允许前端跨域
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(databases.router)
app.include_router(tables.router)
app.include_router(lineage.router)


@app.get("/api/health")
def health():
    return {"status": "ok"}
