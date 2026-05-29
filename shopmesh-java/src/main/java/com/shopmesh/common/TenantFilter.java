package com.shopmesh.common;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.lang.NonNull;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;

/**
 * Extracts tenant_id and user_id from request attributes (set by JwtAuthFilter)
 * and stores them in ThreadLocal TenantContext.
 *
 * This filter runs after JwtAuthFilter (registered in SecurityConfig).
 */
@Component
public class TenantFilter extends OncePerRequestFilter {

    public static final String ATTR_TENANT_ID = "shopmesh.tenant_id";
    public static final String ATTR_USER_ID = "shopmesh.user_id";

    @Override
    protected void doFilterInternal(
            @NonNull HttpServletRequest request,
            @NonNull HttpServletResponse response,
            @NonNull FilterChain filterChain
    ) throws ServletException, IOException {
        try {
            String tenantId = (String) request.getAttribute(ATTR_TENANT_ID);
            String userId = (String) request.getAttribute(ATTR_USER_ID);
            if (tenantId != null) {
                TenantContext.setTenantId(tenantId);
            }
            if (userId != null) {
                TenantContext.setUserId(userId);
            }
            filterChain.doFilter(request, response);
        } finally {
            TenantContext.clear();
        }
    }
}
