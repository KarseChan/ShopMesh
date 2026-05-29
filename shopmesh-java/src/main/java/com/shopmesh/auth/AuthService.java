package com.shopmesh.auth;

import com.shopmesh.auth.dto.*;
import com.shopmesh.user.User;
import com.shopmesh.user.UserRepository;
import io.jsonwebtoken.Claims;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;

import java.util.UUID;

@Slf4j
@Service
@RequiredArgsConstructor
public class AuthService {

    private final UserRepository userRepository;
    private final PasswordEncoder passwordEncoder;
    private final JwtProvider jwtProvider;

    public TokenResponse register(RegisterRequest request) {
        if (userRepository.existsByUsername(request.getUsername())) {
            throw new IllegalArgumentException("Username already exists");
        }

        String tenantId = UUID.randomUUID().toString();
        String userId = UUID.randomUUID().toString();

        User user = User.builder()
                .userId(userId)
                .username(request.getUsername())
                .hashedPassword(passwordEncoder.encode(request.getPassword()))
                .email(request.getEmail())
                .tenantId(tenantId)
                .isActive(true)
                .build();

        userRepository.save(user);
        log.info("User registered: {} (tenant: {})", user.getUsername(), tenantId);

        String accessToken = jwtProvider.createAccessToken(userId, tenantId);
        String refreshToken = jwtProvider.createRefreshToken(userId, tenantId);

        return TokenResponse.of(accessToken, refreshToken);
    }

    public TokenResponse login(LoginRequest request) {
        User user = userRepository.findByUsername(request.getUsername())
                .orElseThrow(() -> new IllegalArgumentException("Invalid username or password"));

        if (!passwordEncoder.matches(request.getPassword(), user.getHashedPassword())) {
            throw new IllegalArgumentException("Invalid username or password");
        }

        String accessToken = jwtProvider.createAccessToken(user.getUserId(), user.getTenantId());
        String refreshToken = jwtProvider.createRefreshToken(user.getUserId(), user.getTenantId());

        return TokenResponse.of(accessToken, refreshToken);
    }

    public TokenResponse refresh(RefreshRequest request) {
        Claims claims;
        try {
            claims = jwtProvider.validateToken(request.getRefreshToken());
        } catch (Exception e) {
            throw new IllegalArgumentException("Invalid or expired refresh token");
        }

        String type = claims.get("type", String.class);
        if (!"refresh".equals(type)) {
            throw new IllegalArgumentException("Invalid token type");
        }

        String userId = claims.getSubject();
        User user = userRepository.findByUserId(userId)
                .orElseThrow(() -> new IllegalArgumentException("User not found"));

        String accessToken = jwtProvider.createAccessToken(user.getUserId(), user.getTenantId());
        String refreshToken = jwtProvider.createRefreshToken(user.getUserId(), user.getTenantId());

        return TokenResponse.of(accessToken, refreshToken);
    }

    public UserInfo getCurrentUser(String userId) {
        User user = userRepository.findByUserId(userId)
                .orElseThrow(() -> new IllegalArgumentException("User not found"));

        return UserInfo.builder()
                .userId(user.getUserId())
                .username(user.getUsername())
                .tenantId(user.getTenantId())
                .email(user.getEmail())
                .build();
    }
}
