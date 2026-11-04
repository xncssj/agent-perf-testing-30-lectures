# 前端项目 Dockerfile - 多阶段构建
# 构建上下文：frontend/

# 阶段1：构建
FROM node:20-alpine AS build
WORKDIR /app

# 先复制依赖文件，利用 Docker 缓存
COPY package.json package-lock.json ./
RUN npm ci

# 复制源码并构建
# VITE_API_BASE：留空则浏览器走相对路径 /api/…，由 nginx 反代后端
# （容器内主机名浏览器无法解析，必须走 nginx；生产 docker-compose 不注入该值）
# 公网网关构建参数（接口契约 §4，经 compose build args 注入）：
#   VITE_PUBLIC_BASE=/single|/multi|/workflow/（资源前缀，同步写入 vite base）
#   VITE_AUTH_MODE=gateway（网关会话 + CSRF；留空为本地登录模式）
#   VITE_STAGE=single|multi|workflow（阶段能力开关与请求模型适配）
# 不得把 localhost、容器主机名或明文 HTTP 地址编入公网产物
ARG VITE_API_BASE=
ENV VITE_API_BASE=$VITE_API_BASE
ARG VITE_PUBLIC_BASE=
ENV VITE_PUBLIC_BASE=$VITE_PUBLIC_BASE
ARG VITE_AUTH_MODE=
ENV VITE_AUTH_MODE=$VITE_AUTH_MODE
ARG VITE_STAGE=
ENV VITE_STAGE=$VITE_STAGE
COPY . .
RUN npm run build

# 阶段2：运行（Nginx）
FROM nginx:alpine

# 复制构建产物
COPY --from=build /app/dist /usr/share/nginx/html

# 注入 Nginx 上游主机名（agent / agent-multi / agent-workflow），模板经 sed 生成
ARG UPSTREAM_HOST=agent
COPY nginx.conf.template /etc/nginx/templates/default.conf.template
RUN sed 's|__UPSTREAM__|'"${UPSTREAM_HOST}"'|g' \
        /etc/nginx/templates/default.conf.template > /etc/nginx/conf.d/default.conf \
    && rm -rf /etc/nginx/templates

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
