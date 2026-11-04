package com.example.orderlogistics;

import org.mybatis.spring.annotation.MapperScan;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.scheduling.annotation.EnableScheduling;

/**
 * 订单物流微服务启动类
 */
@SpringBootApplication
@MapperScan("com.example.orderlogistics.domain.mapper")
// 业务指标 Gauge 的定时取样（BusinessMetrics.refreshOrdersPending）
@EnableScheduling
public class OrderLogisticsApplication {

    private static final Logger log = LoggerFactory.getLogger(OrderLogisticsApplication.class);

    public static void main(String[] args) {
        SpringApplication.run(OrderLogisticsApplication.class, args);
        log.info("订单物流微服务启动成功，API 入口: http://localhost:8080/api/orders");
    }
}
