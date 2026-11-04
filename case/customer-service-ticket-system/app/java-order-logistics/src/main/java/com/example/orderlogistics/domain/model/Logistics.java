package com.example.orderlogistics.domain.model;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableField;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;

/**
 * 物流实体
 */
@Data
@TableName("logistics")
public class Logistics {

    /**
     * 物流单号
     */
    @TableId(type = IdType.ASSIGN_ID)
    private String trackingNo;

    /**
     * 订单ID
     */
    @TableField("order_id")
    private String orderId;

    /**
     * 承运商
     */
    @TableField("carrier")
    private String carrier;

    /**
     * 物流状态：运输中, 派送中, 已签收
     */
    @TableField("status")
    private String status;

    /**
     * 当前位置
     */
    @TableField("current_location")
    private String currentLocation;

    /**
     * 始发地
     */
    @TableField("origin")
    private String origin;

    /**
     * 目的地
     */
    @TableField("destination")
    private String destination;

    /**
     * 物流轨迹（JSON 字符串）
     */
    @TableField("trajectory")
    private String trajectory;

    /**
     * 预计送达时间
     */
    @TableField("estimated_delivery")
    private String estimatedDelivery;
}
