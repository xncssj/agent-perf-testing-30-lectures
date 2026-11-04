package com.example.orderlogistics.domain.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.example.orderlogistics.domain.model.Order;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Select;

/**
 * 订单 Mapper 接口
 */
@Mapper
public interface OrderMapper extends BaseMapper<Order> {

    /**
     * 根据订单ID查询订单
     */
    @Select("SELECT * FROM orders WHERE order_id = #{orderId}")
    Order selectByOrderId(String orderId);

    /**
     * 根据用户ID查询订单列表
     */
    @Select("SELECT * FROM orders WHERE user_id = #{userId}")
    java.util.List<Order> selectByUserId(String userId);

    /**
     * 按状态统计订单数（业务指标 orders_pending Gauge 定时取样用）
     */
    @Select("SELECT COUNT(*) FROM orders WHERE status = #{status}")
    long countByStatus(String status);
}
