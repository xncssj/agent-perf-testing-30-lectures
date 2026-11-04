package com.example.orderlogistics.api.controller;

import com.example.orderlogistics.api.dto.ApiResponse;
import com.example.orderlogistics.domain.model.Order;
import com.example.orderlogistics.domain.service.OrderService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.List;
import java.util.Map;

/**
 * 订单控制器
 */
@RestController
@RequestMapping("/api/orders")
public class OrderController {

    private static final Logger log = LoggerFactory.getLogger(OrderController.class);

    private final OrderService orderService;

    public OrderController(OrderService orderService) {
        this.orderService = orderService;
    }

    /**
     * 根据订单ID查询订单
     */
    @GetMapping("/{orderId}")
    public ResponseEntity<ApiResponse<Order>> getOrder(@PathVariable String orderId) {
        log.info("查询订单: {}", orderId);

        Order order = orderService.getOrderByOrderId(orderId);
        if (order == null) {
            return ResponseEntity.ok(ApiResponse.notFound("订单不存在: " + orderId));
        }

        return ResponseEntity.ok(ApiResponse.success(order));
    }

    /**
     * 根据用户ID查询订单列表
     */
    @GetMapping("/user/{userId}")
    public ResponseEntity<ApiResponse<List<Order>>> getOrdersByUserId(@PathVariable String userId) {
        log.info("查询用户订单列表: {}", userId);

        List<Order> orders = orderService.getOrdersByUserId(userId);
        return ResponseEntity.ok(ApiResponse.success(orders));
    }

    /**
     * 创建订单
     */
    @PostMapping
    public ResponseEntity<ApiResponse<Order>> createOrder(@RequestBody Order order) {
        log.info("创建订单: {}", order);

        Order createdOrder = orderService.createOrder(order);
        return ResponseEntity.ok(ApiResponse.success("订单创建成功", createdOrder));
    }

    /**
     * 更新订单状态
     */
    @PutMapping("/{orderId}/status")
    public ResponseEntity<ApiResponse<Order>> updateOrderStatus(
            @PathVariable String orderId,
            @RequestBody Map<String, String> statusUpdate) {
        log.info("更新订单状态: {} -> {}", orderId, statusUpdate.get("status"));

        String status = statusUpdate.get("status");
        String trackingNo = statusUpdate.get("trackingNo");
        String carrier = statusUpdate.get("carrier");

        Order updatedOrder = orderService.updateOrderStatus(orderId, status, trackingNo, carrier);
        return ResponseEntity.ok(ApiResponse.success("订单状态更新成功", updatedOrder));
    }
}
