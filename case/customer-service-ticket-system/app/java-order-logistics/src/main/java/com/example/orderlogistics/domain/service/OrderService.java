package com.example.orderlogistics.domain.service;

import com.example.orderlogistics.config.BusinessMetrics;
import com.example.orderlogistics.domain.mapper.OrderMapper;
import com.example.orderlogistics.domain.model.Order;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.List;
import java.util.UUID;

/**
 * 订单服务
 */
@Service
public class OrderService {

    private static final Logger log = LoggerFactory.getLogger(OrderService.class);
    private static final DateTimeFormatter DATE_FORMATTER = DateTimeFormatter.ofPattern("yyyyMMdd HH:mm:ss");

    private final OrderMapper orderMapper;
    private final BusinessMetrics metrics;

    public OrderService(OrderMapper orderMapper, BusinessMetrics metrics) {
        this.orderMapper = orderMapper;
        this.metrics = metrics;
    }

    /**
     * 根据订单ID查询订单
     */
    public Order getOrderByOrderId(String orderId) {
        log.debug("查询订单: {}", orderId);
        Order order = orderMapper.selectByOrderId(orderId);
        if (order == null) {
            // 查询未命中以 HTTP 200 + success=false 返回，不产生 4xx，单独计数供看板与告警消费
            metrics.notFound("order");
        }
        return order;
    }

    /**
     * 根据用户ID查询订单列表
     */
    public List<Order> getOrdersByUserId(String userId) {
        log.debug("查询用户订单列表: {}", userId);
        return orderMapper.selectByUserId(userId);
    }

    /**
     * 创建订单
     */
    @Transactional
    public Order createOrder(Order order) {
        log.debug("创建订单: {}", order);

        // 生成订单ID
        if (order.getOrderId() == null || order.getOrderId().isEmpty()) {
            order.setOrderId(generateOrderId());
        }

        // 设置初始状态
        if (order.getStatus() == null || order.getStatus().isEmpty()) {
            order.setStatus("pending");
        }

        // 设置时间
        String now = LocalDateTime.now().format(DATE_FORMATTER);
        order.setCreateTime(now);
        order.setUpdateTime(now);

        // 计算总金额
        if (order.getItems() != null && !order.getItems().isEmpty()) {
            // items 是 JSON 字符串，这里简单处理
            // 实际项目中应该解析 JSON 计算金额
            if (order.getTotalAmount() == null) {
                order.setTotalAmount(0.0);
            }
        }

        orderMapper.insert(order);
        log.info("订单创建成功: {}", order.getOrderId());
        metrics.orderCreated();

        return order;
    }

    /**
     * 更新订单状态
     */
    @Transactional
    public Order updateOrderStatus(String orderId, String status, String trackingNo, String carrier) {
        log.debug("更新订单状态: {} -> {}", orderId, status);

        Order order = orderMapper.selectByOrderId(orderId);
        if (order == null) {
            throw new RuntimeException("订单不存在: " + orderId);
        }

        // 验证状态转换
        validateStatusTransition(order.getStatus(), status);

        // 如果状态为 shipped，必须提供物流单号和承运商
        if ("shipped".equals(status)) {
            if (trackingNo == null || trackingNo.isEmpty()) {
                metrics.ruleViolation("missing_tracking_no");
                throw new RuntimeException("状态变更为 shipped 时必须提供物流单号");
            }
            if (carrier == null || carrier.isEmpty()) {
                metrics.ruleViolation("missing_carrier");
                throw new RuntimeException("状态变更为 shipped 时必须提供承运商");
            }
            order.setTrackingNo(trackingNo);
            order.setCarrier(carrier);
        }

        // 记录转换前状态供业务计数（setStatus 之前取值）
        String fromStatus = order.getStatus();
        order.setStatus(status);
        order.setUpdateTime(LocalDateTime.now().format(DATE_FORMATTER));

        orderMapper.updateById(order);
        log.info("订单状态更新成功: {} -> {}", orderId, status);
        metrics.orderTransition(fromStatus, status);

        return order;
    }

    /**
     * 验证订单状态转换
     */
    private void validateStatusTransition(String currentStatus, String newStatus) {
        boolean valid = false;

        switch (currentStatus) {
            case "pending":
                // pending -> shipped, cancelled
                valid = "shipped".equals(newStatus) || "cancelled".equals(newStatus);
                break;
            case "shipped":
                // shipped -> delivered, cancelled
                valid = "delivered".equals(newStatus) || "cancelled".equals(newStatus);
                break;
            case "delivered":
                // delivered 不能再转换
                valid = false;
                break;
            case "cancelled":
                // cancelled 不能再转换
                valid = false;
                break;
            default:
                valid = false;
        }

        if (!valid) {
            metrics.ruleViolation("order_transition");
            throw new RuntimeException(
                String.format("不允许从 %s 转换到 %s", currentStatus, newStatus));
        }
    }

    /**
     * 生成订单ID
     * 格式：yyyyMMddHHmmss + 4位随机数
     */
    private String generateOrderId() {
        String timestamp = LocalDateTime.now().format(DateTimeFormatter.ofPattern("yyyyMMddHHmmss"));
        String random = String.format("%04d", UUID.randomUUID().hashCode() % 10000);
        return timestamp + random;
    }
}
