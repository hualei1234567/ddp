FROM node:20-alpine AS build

WORKDIR /app

COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci

COPY apps/web/ .
RUN npm run build

FROM nginx:1.27-alpine

# nginx 配置是模板：需要把本地开发令牌（DDP_WEB_DEV_AUTH）注入 /api 请求。
# 官方镜像的 entrypoint 会渲染 /etc/nginx/templates/*.template 到 conf.d；
# NGINX_ENVSUBST_FILTER 限定只替换这一个变量，避免误伤 $host、$uri 等
# nginx 自身变量。
ENV NGINX_ENVSUBST_FILTER=DDP_WEB_DEV_AUTH
COPY infra/docker/nginx.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80
