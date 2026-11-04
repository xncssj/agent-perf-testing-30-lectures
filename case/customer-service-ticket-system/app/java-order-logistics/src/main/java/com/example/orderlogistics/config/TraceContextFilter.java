package com.example.orderlogistics.config;

import jakarta.servlet.Filter;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.ServletRequest;
import jakarta.servlet.ServletResponse;
import jakarta.servlet.http.HttpServletRequest;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.slf4j.MDC;
import org.springframework.stereotype.Component;

import java.io.IOException;

/**
 * 依赖调用上下文过滤器（006 R03）。
 *
 * Agent 侧经 HTTP 传播 traceparent / x-agent-run-id / x-agent-tool-call-id；
 * 本过滤器只把相关 ID 写入 MDC 与结构化日志（取证用），不做任何指标标签，
 * 避免逐笔 ID 进入 Prometheus 造成高基数。缺头的请求（人工调用、健康检查）
 * 不伪造身份，直接放行。
 */
@Component
public class TraceContextFilter implements Filter {

    private static final Logger log =
            LoggerFactory.getLogger(TraceContextFilter.class);

    @Override
    public void doFilter(ServletRequest request, ServletResponse response,
                         FilterChain chain) throws IOException, ServletException {
        if (!(request instanceof HttpServletRequest http)) {
            chain.doFilter(request, response);
            return;
        }
        String traceparent = http.getHeader("traceparent");
        String agentRunId = http.getHeader("x-agent-run-id");
        String toolCallId = http.getHeader("x-agent-tool-call-id");
        if (traceparent == null && agentRunId == null && toolCallId == null) {
            chain.doFilter(request, response);
            return;
        }
        try {
            if (traceparent != null) {
                MDC.put("traceparent", traceparent);
            }
            if (agentRunId != null) {
                MDC.put("agentRunId", agentRunId);
            }
            if (toolCallId != null) {
                MDC.put("toolCallId", toolCallId);
            }
            log.info("dependency-request|path={}|traceparent={}|agentRunId={}|toolCallId={}",
                    http.getRequestURI(), traceparent, agentRunId, toolCallId);
            chain.doFilter(request, response);
        } finally {
            MDC.remove("traceparent");
            MDC.remove("agentRunId");
            MDC.remove("toolCallId");
        }
    }
}
