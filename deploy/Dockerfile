# 多源异构数据驱动岗位和能力图谱系统 —— 后端 + 前端一体化镜像
# 多阶段构建：第一阶段编译前端，第二阶段运行后端并托管静态资源
FROM node:20-alpine AS frontend
WORKDIR /fe
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim AS runtime
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
# 仅使用预编译 wheel，避免容器内编译
COPY backend/requirements.txt ./
RUN pip install --only-binary=:all: -r requirements.txt || pip install -r requirements.txt
COPY backend/app ./app
COPY backend/.env ./.env
# 前端构建产物作为静态资源由后端托管
COPY --from=frontend /fe/dist ./static
EXPOSE 8200
ENV STATIC_DIR=/app/static
# 单 worker，控制内存占用
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8200", "--workers", "1"]
