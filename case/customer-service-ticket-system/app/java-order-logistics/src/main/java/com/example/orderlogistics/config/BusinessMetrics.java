package com.example.orderlogistics.config;

import com.example.orderlogistics.domain.mapper.OrderMapper;
import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.Gauge;
import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.Tag;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.concurrent.atomic.AtomicLong;
import java.util.stream.Collectors;
import java.util.stream.Stream;

/**
 * 业务指标埋点：订单/物流业务层计数。
 * 接口性能（QPS/时延/状态码）由 Actuator 的 http.server.requests 自动覆盖，不在本类重复埋点。
 *
 * Micrometer 计量名 → Prometheus 导出名映射（实测口径见 README「指标清单」）：
 * 圆点转下划线；Counter 自动追加 _total；Gauge 原样导出。本类计量名直接用下划线风格，
 * 与导出名只差 Counter 的 _total 后缀。
 *
 * 纪律：
 * - 预建全部合法标签组合的 0 值序列，避免无流量时段看板 No data（沿用 app/harness/metrics.py 先例）；
 * - 观测埋点是业务路径的旁路：计数与取样失败不影响业务执行，不向调用方抛错；
 * - 标签只取低基数枚举（状态、资源类型、违例类型），不写订单号等业务 ID（高基数）。
 */
@Component
public class BusinessMetrics {

    private static final Logger log = LoggerFactory.getLogger(BusinessMetrics.class);

    /** 订单状态机全部合法转换组合，与 OrderService.validateStatusTransition 保持一致 */
    private static final String[][] ORDER_TRANSITIONS = {
            {"pending", "shipped"}, {"pending", "cancelled"},
            {"shipped", "delivered"}, {"shipped", "cancelled"}
    };

    /** 物流状态机全部合法转换组合，与 LogisticsService.validateLogisticsStatusTransition 保持一致 */
    private static final String[][] LOGISTICS_TRANSITIONS = {
            {"运输中", "派送中"}, {"派送中", "已签收"}
    };

    private final OrderMapper orderMapper;
    private final AtomicLong ordersPending = new AtomicLong(0);
    private final Counter ordersCreated;
    private final Map<String, Counter> orderTransitions;
    private final Map<String, Counter> logisticsTransitions;
    private final Map<String, Counter> notFound;
    private final Map<String, Counter> ruleViolations;

    public BusinessMetrics(MeterRegistry registry, OrderMapper orderMapper) {
        this.orderMapper = orderMapper;

        this.ordersCreated = Counter.builder("order_logistics_orders_created")
                .description("建单成功计数（POST /api/orders 业务口径）")
                .register(registry);

        this.orderTransitions = preBuild(registry, "order_logistics_order_transitions",
                "订单状态机合法转换计数", new String[]{"from", "to"}, ORDER_TRANSITIONS);
        this.logisticsTransitions = preBuild(registry, "order_logistics_logistics_transitions",
                "物流状态机合法转换计数", new String[]{"from", "to"}, LOGISTICS_TRANSITIONS);
        this.notFound = preBuild(registry, "order_logistics_notfound",
                "GET 查询未命中（HTTP 200 + success=false 的业务失败，非 HTTP 4xx）",
                new String[]{"resource"}, new String[][]{{"order"}, {"logistics"}});
        this.ruleViolations = preBuild(registry, "order_logistics_rule_violations",
                "业务规则违例（非法状态转换、shipped 缺必填字段）",
                new String[]{"type"}, new String[][]{
                        {"order_transition"}, {"logistics_transition"},
                        {"missing_tracking_no"}, {"missing_carrier"}});

        Gauge.builder("order_logistics_orders_pending", ordersPending, AtomicLong::doubleValue)
                .description("pending 状态订单存量（30 秒定时取样，非实时）")
                .register(registry);
    }

    /** 预建每组标签值的 0 值计数器；键为「值1|值2」拼接 */
    private static Map<String, Counter> preBuild(MeterRegistry registry, String name, String desc,
                                                 String[] tagKeys, String[][] tagValueCombos) {
        return Stream.of(tagValueCombos).collect(Collectors.toMap(
                combo -> String.join("|", combo),
                combo -> {
                    Tag[] tags = new Tag[combo.length];
                    for (int i = 0; i < combo.length; i++) {
                        tags[i] = Tag.of(tagKeys[i], combo[i]);
                    }
                    return Counter.builder(name).description(desc).tags(Arrays.asList(tags)).register(registry);
                },
                (a, b) -> a,
                LinkedHashMap::new));
    }

    /** 建单成功后调用 */
    public void orderCreated() {
        ordersCreated.increment();
    }

    /** 订单状态机转换成功后调用 */
    public void orderTransition(String from, String to) {
        increment(orderTransitions, from + "|" + to);
    }

    /** 物流状态机转换成功后调用 */
    public void logisticsTransition(String from, String to) {
        increment(logisticsTransitions, from + "|" + to);
    }

    /** GET 查询未命中（业务失败被 HTTP 200 掩盖，单独计数） */
    public void notFound(String resource) {
        increment(notFound, resource);
    }

    /** 业务规则违例（转换校验失败、必填字段缺失） */
    public void ruleViolation(String type) {
        increment(ruleViolations, type);
    }

    /** 组合未命中时静默忽略：合法组合已在构造期全覆盖，走到这里说明业务与埋点口径漂移，debug 留痕即可 */
    private static void increment(Map<String, Counter> counters, String key) {
        Counter counter = counters.get(key);
        if (counter == null) {
            log.debug("业务指标未预建组合，忽略计数: {}", key);
            return;
        }
        counter.increment();
    }

    /** 定时刷新 pending 订单存量；取样失败跳过本轮、保留上次读数，不影响业务 */
    @Scheduled(fixedDelay = 30_000, initialDelay = 10_000)
    public void refreshOrdersPending() {
        try {
            ordersPending.set(orderMapper.countByStatus("pending"));
        } catch (Exception e) {
            log.debug("orders_pending 取样失败，跳过本轮: {}", e.getMessage());
        }
    }
}
