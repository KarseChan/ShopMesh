package com.shopmesh.auth;

import io.jsonwebtoken.Claims;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.lang.NonNull;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.security.web.authentication.WebAuthenticationDetailsSource;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;
import java.util.List;

import static com.shopmesh.common.TenantFilter.ATTR_TENANT_ID;
import static com.shopmesh.common.TenantFilter.ATTR_USER_ID;

/**
 * JWT authentication filter.
 * Extracts Bearer token from Authorization header, validates it,
 * and sets the Spring Security authentication context.
 * Also sets tenant_id and user_id as request attributes for TenantFilter.
 */
@Slf4j
@Component
@RequiredArgsConstructor
public class JwtAuthFilter extends OncePerRequestFilter {

    private final JwtProvider jwtProvider;

    @Override
    protected void doFilterInternal(
            @NonNull HttpServletRequest request,
            @NonNull HttpServletResponse response,
            @NonNull FilterChain filterChain
    ) throws ServletException, IOException {
        String authHeader = request.getHeader("Authorization");

        if (authHeader == null || !authHeader.startsWith("Bearer ")) {
            filterChain.doFilter(request, response);
            return;
        }

        String token = authHeader.substring(7);

        try {
            Claims claims = jwtProvider.validateToken(token);

            // Only accept access tokens
            String type = claims.get("type", String.class);
            if (!"access".equals(type)) {
                filterChain.doFilter(request, response);
                return;
            }

            String userId = claims.getSubject();
            String tenantId = claims.get("tenant_id", String.class);

            // Set request attributes for TenantFilter
            request.setAttribute(ATTR_USER_ID, userId);
            request.setAttribute(ATTR_TENANT_ID, tenantId);

            // Set Spring Security authentication
            UsernamePasswordAuthenticationToken authentication =
                    new UsernamePasswordAuthenticationToken(userId, null, List.of());
            authentication.setDetails(new WebAuthenticationDetailsSource().buildDetails(request));
            SecurityContextHolder.getContext().setAuthentication(authentication);

        } catch (Exception e) {
            log.debug("JWT validation failed: {}", e.getMessage());
            // Leave authentication null — Spring Security will handle 401
        }

        filterChain.doFilter(request, response);
    }
}
