package com.example.orderlogistics.api.controller;

import com.example.orderlogistics.api.dto.ApiResponse;
import com.example.orderlogistics.domain.model.Logistics;
import com.example.orderlogistics.domain.service.LogisticsService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.Map;

/**
 * 物流控制器
 */
@RestController
@RequestMapping("/api/logistics")
public class LogisticsController {

    private static final Logger log = LoggerFactory.getLogger(LogisticsController.class);

    private final LogisticsService logisticsService;

    public LogisticsController(LogisticsService logisticsService) {
        this.logisticsService = logisticsService;
    }

    /**
     * 根据物流单号查询物流信息
     */
    @GetMapping("/{trackingNo}")
    public ResponseEntity<ApiResponse<Logistics>> getLogisticsByTrackingNo(@PathVariable String trackingNo) {
        log.info("根据物流单号查询物流信息: {}", trackingNo);

        Logistics logistics = logisticsService.getLogisticsByTrackingNo(trackingNo);
        if (logistics == null) {
            return ResponseEntity.ok(ApiResponse.notFound("物流信息不存在: " + trackingNo));
        }

        return ResponseEntity.ok(ApiResponse.success(logistics));
    }

    /**
     * 根据订单ID查询物流信息
     */
    @GetMapping("/order/{orderId}")
    public ResponseEntity<ApiResponse<Logistics>> getLogisticsByOrderId(@PathVariable String orderId) {
        log.info("根据订单ID查询物流信息: {}", orderId);

        Logistics logistics = logisticsService.getLogisticsByOrderId(orderId);
        if (logistics == null) {
            return ResponseEntity.ok(ApiResponse.notFound("物流信息不存在，订单ID: " + orderId));
        }

        return ResponseEntity.ok(ApiResponse.success(logistics));
    }

    /**
     * 更新物流状态
     */
    @PutMapping("/{trackingNo}")
    public ResponseEntity<ApiResponse<Logistics>> updateLogisticsStatus(
            @PathVariable String trackingNo,
            @RequestBody Map<String, Object> updateRequest) {
        log.info("更新物流状态: {}", trackingNo);

        String status = (String) updateRequest.get("status");
        String currentLocation = (String) updateRequest.get("currentLocation");
        String trajectory = updateRequest.get("trajectory") != null ?
            updateRequest.get("trajectory").toString() : null;

        Logistics updatedLogistics = logisticsService.updateLogisticsStatus(
            trackingNo, status, currentLocation, trajectory);

        return ResponseEntity.ok(ApiResponse.success("物流状态更新成功", updatedLogistics));
    }
}
