package com.shopmesh.apikey;

import com.shopmesh.common.TenantContext;
import lombok.RequiredArgsConstructor;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.*;

import java.util.List;
import java.util.Map;

@RestController
@RequestMapping("/api/auth/api-keys")
@RequiredArgsConstructor
public class ApiKeyController {

    private final ApiKeyService apiKeyService;

    @PostMapping
    public ResponseEntity<?> createApiKey(@RequestBody CreateApiKeyRequest request) {
        String tenantId = TenantContext.getTenantId();
        if (tenantId.isEmpty()) {
            return ResponseEntity.status(HttpStatus.UNAUTHORIZED)
                    .body(Map.of("error", "Not authenticated"));
        }

        ApiKeyService.CreateApiKeyResult result = apiKeyService.createApiKey(tenantId, request.getName());

        return ResponseEntity.status(HttpStatus.CREATED).body(CreateApiKeyResponse.builder()
                .keyId(result.apiKey().getKeyId())
                .name(result.apiKey().getName())
                .apiKey(result.fullKey())
                .message("Save this key — it will not be shown again.")
                .build());
    }

    @GetMapping
    public ResponseEntity<?> listApiKeys() {
        String tenantId = TenantContext.getTenantId();
        if (tenantId.isEmpty()) {
            return ResponseEntity.status(HttpStatus.UNAUTHORIZED)
                    .body(Map.of("error", "Not authenticated"));
        }

        List<ApiKey> keys = apiKeyService.listApiKeys(tenantId);

        List<ApiKeyResponse> response = keys.stream()
                .map(k -> ApiKeyResponse.builder()
                        .keyId(k.getKeyId())
                        .name(k.getName())
                        .isActive(k.getIsActive())
                        .createdAt(k.getCreatedAt())
                        .lastUsedAt(k.getLastUsedAt())
                        .build())
                .toList();

        return ResponseEntity.ok(response);
    }

    @DeleteMapping("/{keyId}")
    public ResponseEntity<?> revokeApiKey(@PathVariable String keyId) {
        String tenantId = TenantContext.getTenantId();
        if (tenantId.isEmpty()) {
            return ResponseEntity.status(HttpStatus.UNAUTHORIZED)
                    .body(Map.of("error", "Not authenticated"));
        }

        boolean revoked = apiKeyService.revokeApiKey(keyId, tenantId);
        if (!revoked) {
            return ResponseEntity.status(HttpStatus.NOT_FOUND)
                    .body(Map.of("error", "API key not found"));
        }

        return ResponseEntity.noContent().build();
    }
}
