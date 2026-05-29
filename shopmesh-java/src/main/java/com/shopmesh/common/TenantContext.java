package com.shopmesh.common;

/**
 * Thread-local tenant context, set by TenantFilter from JWT claims.
 * Read by controllers and services to scope data access.
 */
public final class TenantContext {

    private static final ThreadLocal<String> TENANT_ID = new ThreadLocal<>();
    private static final ThreadLocal<String> USER_ID = new ThreadLocal<>();

    private TenantContext() {}

    public static void setTenantId(String tenantId) {
        TENANT_ID.set(tenantId);
    }

    public static String getTenantId() {
        return TENANT_ID.get() != null ? TENANT_ID.get() : "";
    }

    public static void setUserId(String userId) {
        USER_ID.set(userId);
    }

    public static String getUserId() {
        return USER_ID.get() != null ? USER_ID.get() : "";
    }

    public static void clear() {
        TENANT_ID.remove();
        USER_ID.remove();
    }
}
