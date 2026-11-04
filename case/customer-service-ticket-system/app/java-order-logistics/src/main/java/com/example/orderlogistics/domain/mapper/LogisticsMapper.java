package com.example.orderlogistics.domain.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.example.orderlogistics.domain.model.Logistics;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Select;

/**
 * 物流 Mapper 接口
 */
@Mapper
public interface LogisticsMapper extends BaseMapper<Logistics> {

    /**
     * 根据物流单号查询物流信息
     */
    @Select("SELECT * FROM logistics WHERE tracking_no = #{trackingNo}")
    Logistics selectByTrackingNo(String trackingNo);

    /**
     * 根据订单ID查询物流信息
     */
    @Select("SELECT * FROM logistics WHERE order_id = #{orderId}")
    Logistics selectByOrderId(String orderId);
}
