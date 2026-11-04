package com.example.orderlogistics.config;

import com.zaxxer.hikari.HikariConfig;
import com.zaxxer.hikari.HikariDataSource;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.Primary;
import org.springframework.core.env.Environment;
import org.springframework.jdbc.core.JdbcTemplate;

import javax.sql.DataSource;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.sql.Connection;
import java.sql.Statement;

/**
 * 数据源配置：支持 SQLite 和 MySQL 切换
 *
 * 通过 spring.profiles.active 属性切换数据库类型：
 * - sqlite: 使用 SQLite 内存数据库（默认）
 * - mysql: 使用 MySQL 数据库
 */
@Configuration
public class DataSourceConfig {

    private static final Logger log = LoggerFactory.getLogger(DataSourceConfig.class);

    private final Environment environment;

    public DataSourceConfig(Environment environment) {
        this.environment = environment;
    }

    @Bean
    @Primary
    public DataSource dataSource() {
        HikariConfig config = new HikariConfig();

        boolean isMysql = environment.acceptsProfiles("mysql");

        if (isMysql) {
            // MySQL 配置
            String url = environment.getProperty("spring.datasource.url");
            String username = environment.getProperty("spring.datasource.username");
            String password = environment.getProperty("spring.datasource.password");

            config.setJdbcUrl(url);
            config.setUsername(username);
            config.setPassword(password);
            config.setDriverClassName("com.mysql.cj.jdbc.Driver");
            config.setMaximumPoolSize(10);
            config.setMinimumIdle(5);

            log.info("使用 MySQL 数据库: {}", url);
        } else {
            // SQLite 配置（默认）
            config.setJdbcUrl("jdbc:sqlite::memory:");
            config.setDriverClassName("org.sqlite.JDBC");
            config.setMaximumPoolSize(1);

            log.info("使用 SQLite 内存数据库");
        }

        HikariDataSource dataSource = new HikariDataSource(config);

        // 初始化表结构
        initSchema(dataSource, isMysql);

        return dataSource;
    }

    /**
     * 初始化数据库表结构
     */
    private void initSchema(DataSource dataSource, boolean isMysql) {
        try (Connection conn = dataSource.getConnection();
             Statement stmt = conn.createStatement()) {

            String schemaFile;
            if (isMysql) {
                schemaFile = "sql/schema-mysql.sql";
            } else {
                schemaFile = "sql/schema-sqlite.sql";
            }

            try (InputStream is = getClass().getClassLoader().getResourceAsStream(schemaFile)) {
                if (is != null) {
                    String sql = new String(is.readAllBytes(), StandardCharsets.UTF_8);
                    // 按分号分割并执行每条SQL
                    String[] statements = sql.split(";");
                    for (String statement : statements) {
                        String trimmed = statement.trim();
                        if (!trimmed.isEmpty()) {
                            stmt.execute(trimmed);
                        }
                    }
                }
            }

            log.info("数据库初始化完成: {}", isMysql ? "MySQL" : "SQLite");

        } catch (Exception e) {
            throw new RuntimeException("数据库初始化失败", e);
        }
    }

    @Bean
    public JdbcTemplate jdbcTemplate(DataSource dataSource) {
        return new JdbcTemplate(dataSource);
    }
}
