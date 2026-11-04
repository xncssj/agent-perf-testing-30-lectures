package com.example.orderlogistics.config;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.example.orderlogistics.domain.mapper.LogisticsMapper;
import com.example.orderlogistics.domain.mapper.OrderMapper;
import com.example.orderlogistics.domain.model.Logistics;
import com.example.orderlogistics.domain.model.Order;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.CommandLineRunner;
import org.springframework.core.io.ClassPathResource;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;

import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.List;
import java.util.Map;

/**
 * 数据初始化器：启动时从 test-data 加载测试数据
 */
@Component
public class DataInitializer implements CommandLineRunner {

    private static final Logger log = LoggerFactory.getLogger(DataInitializer.class);

    private final OrderMapper orderMapper;
    private final LogisticsMapper logisticsMapper;
    private final ObjectMapper objectMapper;
    private final JdbcTemplate jdbcTemplate;

    @Value("${sqlite.import-test-data:true}")
    private boolean importTestData;

    @Value("${sqlite.test-data-path:../../testing/test-data/}")
    private String testDataPath;

    public DataInitializer(OrderMapper orderMapper, LogisticsMapper logisticsMapper,
                          ObjectMapper objectMapper, JdbcTemplate jdbcTemplate) {
        this.orderMapper = orderMapper;
        this.logisticsMapper = logisticsMapper;
        this.objectMapper = objectMapper;
        this.jdbcTemplate = jdbcTemplate;
    }

    @Override
    public void run(String... args) throws Exception {
        if (!importTestData) {
            log.info("跳过测试数据导入（import-test-data=false）");
            return;
        }

        log.info("开始导入测试数据...");

        // 使用 JdbcTemplate 创建表
        createTables();

        // 导入订单数据
        importOrders();

        // 导入物流数据
        importLogistics();

        log.info("测试数据导入完成");
    }

    /**
     * 创建表
     */
    private void createTables() {
        try {
            // 创建订单表
            jdbcTemplate.execute("CREATE TABLE IF NOT EXISTS orders (" +
                "order_id TEXT PRIMARY KEY, " +
                "user_id TEXT, " +
                "status TEXT, " +
                "items TEXT, " +
                "total_amount REAL, " +
                "shipping_address TEXT, " +
                "tracking_no TEXT, " +
                "carrier TEXT, " +
                "create_time TEXT, " +
                "update_time TEXT" +
                ")");

            // 创建物流表
            jdbcTemplate.execute("CREATE TABLE IF NOT EXISTS logistics (" +
                "tracking_no TEXT PRIMARY KEY, " +
                "order_id TEXT, " +
                "carrier TEXT, " +
                "status TEXT, " +
                "current_location TEXT, " +
                "origin TEXT, " +
                "destination TEXT, " +
                "trajectory TEXT, " +
                "estimated_delivery TEXT" +
                ")");

            log.info("表创建完成");
        } catch (Exception e) {
            log.warn("创建表失败: {}", e.getMessage());
        }
    }

    /**
     * 导入订单数据
     */
    private void importOrders() throws Exception {
        try {
            // 尝试从文件系统加载
            Path ordersPath = Paths.get(testDataPath, "orders.json");
            List<Map<String, Object>> ordersData;

            if (Files.exists(ordersPath)) {
                log.info("从文件系统加载订单数据: {}", ordersPath);
                ordersData = objectMapper.readValue(ordersPath.toFile(),
                    new TypeReference<List<Map<String, Object>>>() {});
            } else {
                // 从 classpath 加载
                log.info("从 classpath 加载订单数据");
                InputStream is = new ClassPathResource("data/orders.json").getInputStream();
                ordersData = objectMapper.readValue(is,
                    new TypeReference<List<Map<String, Object>>>() {});
            }

            for (Map<String, Object> orderData : ordersData) {
                Order order = convertToOrder(orderData);
                orderMapper.insert(order);
            }

            log.info("导入 {} 条订单数据", ordersData.size());

        } catch (Exception e) {
            log.warn("导入订单数据失败: {}", e.getMessage());
        }
    }

    /**
     * 导入物流数据
     */
    private void importLogistics() throws Exception {
        try {
            // 尝试从文件系统加载
            Path logisticsPath = Paths.get(testDataPath, "logistics.json");
            List<Map<String, Object>> logisticsData;

            if (Files.exists(logisticsPath)) {
                log.info("从文件系统加载物流数据: {}", logisticsPath);
                logisticsData = objectMapper.readValue(logisticsPath.toFile(),
                    new TypeReference<List<Map<String, Object>>>() {});
            } else {
                // 从 classpath 加载
                log.info("从 classpath 加载物流数据");
                InputStream is = new ClassPathResource("data/logistics.json").getInputStream();
                logisticsData = objectMapper.readValue(is,
                    new TypeReference<List<Map<String, Object>>>() {});
            }

            for (Map<String, Object> logisticsDataItem : logisticsData) {
                Logistics logistics = convertToLogistics(logisticsDataItem);
                logisticsMapper.insert(logistics);
            }

            log.info("导入 {} 条物流数据", logisticsData.size());

        } catch (Exception e) {
            log.warn("导入物流数据失败: {}", e.getMessage());
        }
    }

    /**
     * 将 Map 转换为 Order 实体
     */
    private Order convertToOrder(Map<String, Object> data) {
        Order order = new Order();
        order.setOrderId((String) data.get("order_id"));
        order.setUserId((String) data.get("user_id"));
        order.setStatus((String) data.get("status"));
        order.setTotalAmount(data.get("total_amount") != null ?
            ((Number) data.get("total_amount")).doubleValue() : null);
        order.setTrackingNo((String) data.get("tracking_no"));
        order.setCarrier((String) data.get("carrier"));
        order.setCreateTime((String) data.get("create_time"));
        order.setUpdateTime((String) data.get("update_time"));

        // 将 items 和 shippingAddress 转换为 JSON 字符串
        try {
            if (data.get("items") != null) {
                order.setItems(objectMapper.writeValueAsString(data.get("items")));
            }
            if (data.get("shipping_address") != null) {
                order.setShippingAddress(objectMapper.writeValueAsString(data.get("shipping_address")));
            }
        } catch (Exception e) {
            log.warn("转换订单数据失败: {}", e.getMessage());
        }

        return order;
    }

    /**
     * 将 Map 转换为 Logistics 实体
     */
    private Logistics convertToLogistics(Map<String, Object> data) {
        Logistics logistics = new Logistics();
        logistics.setTrackingNo((String) data.get("tracking_no"));
        logistics.setOrderId((String) data.get("order_id"));
        logistics.setCarrier((String) data.get("carrier"));
        logistics.setStatus((String) data.get("status"));
        logistics.setCurrentLocation((String) data.get("current_location"));
        logistics.setOrigin((String) data.get("origin"));
        logistics.setDestination((String) data.get("destination"));
        logistics.setEstimatedDelivery((String) data.get("estimated_delivery"));

        // 将 trajectory 转换为 JSON 字符串
        try {
            if (data.get("trajectory") != null) {
                logistics.setTrajectory(objectMapper.writeValueAsString(data.get("trajectory")));
            }
        } catch (Exception e) {
            log.warn("转换物流数据失败: {}", e.getMessage());
        }

        return logistics;
    }
}
