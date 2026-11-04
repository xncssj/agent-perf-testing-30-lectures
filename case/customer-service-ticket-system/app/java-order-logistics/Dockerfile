# 订单与物流微服务 · 多阶段构建
# 构建上下文：本目录（java-order-logistics/）

FROM maven:3.9-eclipse-temurin-17 AS build
WORKDIR /build

COPY pom.xml .
COPY src ./src
# Maven 中央仓库在部分国内网络不可达，统一走 Aliyun 镜像（与基础镜像的国内化策略一致）；
# Maven 不读 HTTP(S)_PROXY 环境变量，构建代理须写入 settings.xml 的 <proxies> 才生效——
# 仅当构建传入代理参数（云端 overlay）时注入，本地直连构建不受影响。
RUN mkdir -p /root/.m2 \
    && proxy_host=""; proxy_port=""; \
    if [ -n "${HTTPS_PROXY:-}" ]; then \
        rest="${HTTPS_PROXY#*://}"; rest="${rest%%/*}"; \
        proxy_host="${rest%%:*}"; proxy_port="${rest##*:}"; \
    fi; \
    { \
        echo '<?xml version="1.0" encoding="UTF-8"?>'; \
        echo '<settings xmlns="http://maven.apache.org/SETTINGS/1.0.0">'; \
        echo '  <mirrors>'; \
        echo '    <mirror>'; \
        echo '      <id>aliyunmaven</id>'; \
        echo '      <mirrorOf>central</mirrorOf>'; \
        echo '      <name>Aliyun Maven Central</name>'; \
        echo '      <url>https://maven.aliyun.com/repository/public</url>'; \
        echo '    </mirror>'; \
        echo '  </mirrors>'; \
        if [ -n "$proxy_host" ]; then \
            echo '  <proxies>'; \
            echo '    <proxy>'; \
            echo '      <id>build-proxy</id>'; \
            echo '      <active>true</active>'; \
            echo '      <protocol>http</protocol>'; \
            echo "      <host>$proxy_host</host>"; \
            echo "      <port>$proxy_port</port>"; \
            echo '      <nonProxyHosts>localhost|127.0.0.1|*.internal</nonProxyHosts>'; \
            echo '    </proxy>'; \
            echo '  </proxies>'; \
        fi; \
        echo '</settings>'; \
    } > /root/.m2/settings.xml
RUN mvn -B -DskipTests package

FROM eclipse-temurin:17-jre-jammy
WORKDIR /app

# 健康检查用 curl（compose healthcheck）
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=build /build/target/order-logistics-1.0.0.jar /app/app.jar

EXPOSE 8080

ENTRYPOINT ["java", "-jar", "/app/app.jar"]
