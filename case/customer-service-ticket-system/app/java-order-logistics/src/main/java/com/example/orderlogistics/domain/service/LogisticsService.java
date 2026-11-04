package com.example.orderlogistics.domain.service;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.example.orderlogistics.config.BusinessMetrics;
import com.example.orderlogistics.domain.mapper.LogisticsMapper;
import com.example.orderlogistics.domain.model.Logistics;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.ArrayList;
import java.util.List;

/**
 * 物流服务
 */
@Service
public class LogisticsService {

    private static final Logger log = LoggerFactory.getLogger(LogisticsService.class);

    private final LogisticsMapper logisticsMapper;
    private final ObjectMapper objectMapper;
    private final BusinessMetrics metrics;

    public LogisticsService(LogisticsMapper logisticsMapper, ObjectMapper objectMapper,
                            BusinessMetrics metrics) {
        this.logisticsMapper = logisticsMapper;
        this.objectMapper = objectMapper;
        this.metrics = metrics;
    }

    /**
     * 根据物流单号查询物流信息
     */
    public Logistics getLogisticsByTrackingNo(String trackingNo) {
        log.debug("根据物流单号查询物流信息: {}", trackingNo);
        Logistics logistics = logisticsMapper.selectByTrackingNo(trackingNo);
        if (logistics == null) {
            // 查询未命中以 HTTP 200 + success=false 返回，不产生 4xx，单独计数供看板与告警消费
            metrics.notFound("logistics");
        }
        return logistics;
    }

    /**
     * 根据订单ID查询物流信息
     */
    public Logistics getLogisticsByOrderId(String orderId) {
        log.debug("根据订单ID查询物流信息: {}", orderId);
        Logistics logistics = logisticsMapper.selectByOrderId(orderId);
        if (logistics == null) {
            metrics.notFound("logistics");
        }
        return logistics;
    }

    /**
     * 更新物流状态
     */
    @Transactional
    public Logistics updateLogisticsStatus(String trackingNo, String status,
                                          String currentLocation, String trajectoryJson) {
        log.debug("更新物流状态: {} -> {}", trackingNo, status);

        Logistics logistics = logisticsMapper.selectByTrackingNo(trackingNo);
        if (logistics == null) {
            throw new RuntimeException("物流信息不存在: " + trackingNo);
        }

        // 验证物流状态转换
        validateLogisticsStatusTransition(logistics.getStatus(), status);

        // 记录转换前状态供业务计数（setStatus 之前取值）
        String fromStatus = logistics.getStatus();
        logistics.setStatus(status);
        if (currentLocation != null && !currentLocation.isEmpty()) {
            logistics.setCurrentLocation(currentLocation);
        }

        // 更新轨迹
        if (trajectoryJson != null && !trajectoryJson.isEmpty()) {
            // 追加新轨迹到现有轨迹
            String existingTrajectory = logistics.getTrajectory();
            if (existingTrajectory == null || existingTrajectory.isEmpty()) {
                logistics.setTrajectory(trajectoryJson);
            } else {
                // 使用 Jackson 安全合并轨迹 JSON 数组
                logistics.setTrajectory(mergeTrajectories(existingTrajectory, trajectoryJson));
            }
        }

        logisticsMapper.updateById(logistics);
        log.info("物流状态更新成功: {} -> {}", trackingNo, status);
        metrics.logisticsTransition(fromStatus, status);

        return logistics;
    }

    /**
     * 验证物流状态转换
     */
    private void validateLogisticsStatusTransition(String currentStatus, String newStatus) {
        boolean valid = false;

        switch (currentStatus) {
            case "运输中":
                // 运输中 -> 派送中
                valid = "派送中".equals(newStatus);
                break;
            case "派送中":
                // 派送中 -> 已签收
                valid = "已签收".equals(newStatus);
                break;
            case "已签收":
                // 已签收 不能再转换
                valid = false;
                break;
            default:
                valid = false;
        }

        if (!valid) {
            metrics.ruleViolation("logistics_transition");
            throw new RuntimeException(
                String.format("不允许从 %s 转换到 %s", currentStatus, newStatus));
        }
    }

    /**
     * 合并轨迹 JSON 数组（使用 Jackson 安全解析）
     */
    private String mergeTrajectories(String existing, String newTrajectory) {
        try {
            // 解析现有轨迹
            List<Object> existingList = objectMapper.readValue(
                existing, new TypeReference<List<Object>>() {});

            // 解析新轨迹
            List<Object> newList = objectMapper.readValue(
                newTrajectory, new TypeReference<List<Object>>() {});

            // 合并两个列表
            List<Object> merged = new ArrayList<>(existingList);
            merged.addAll(newList);

            // 序列化为 JSON
            return objectMapper.writeValueAsString(merged);
        } catch (Exception e) {
            log.error("合并轨迹 JSON 失败: existing={}, new={}", existing, newTrajectory, e);
            // 回退：返回新轨迹（覆盖旧数据，比返回损坏的 JSON 更安全）
            return newTrajectory;
        }
    }
}
