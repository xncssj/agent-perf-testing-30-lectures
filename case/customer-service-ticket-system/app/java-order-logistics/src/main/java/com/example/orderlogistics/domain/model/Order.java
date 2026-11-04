package com.example.orderlogistics.domain.model;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableField;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;

/**
 * 订单实体
 */
@Data
@TableName("orders")
public class Order {

    /**
     * 订单ID
     */
    @TableId(type = IdType.ASSIGN_ID)
    private String orderId;

    /**
     * 用户ID
     */
    @TableField("user_id")
    private String userId;

    /**
     * 订单状态：pending, shipped, delivered, cancelled
     */
    @TableField("status")
    private String status;

    /**
     * 订单商品列表（JSON 字符串）
     */
    @TableField("items")
    private String items;

    /**
     * 总金额
     */
    @TableField("total_amount")
    private Double totalAmount;

    /**
     * 收货地址（JSON 字符串）
     */
    @TableField("shipping_address")
    private String shippingAddress;

    /**
     * 物流单号
     */
    @TableField("tracking_no")
    private String trackingNo;

    /**
     * 承运商
     */
    @TableField("carrier")
    private String carrier;

    /**
     * 创建时间
     */
    @TableField("create_time")
    private String createTime;

    /**
     * 更新时间
     */
    @TableField("update_time")
    private String updateTime;
}
