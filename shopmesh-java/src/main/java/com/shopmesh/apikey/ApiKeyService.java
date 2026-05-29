package com.shopmesh.apikey;

import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;

import java.security.SecureRandom;
import java.time.LocalDateTime;
import java.util.HexFormat;
import java.util.List;

@Slf4j
@Service
@RequiredArgsConstructor
public class ApiKeyService {

    private static final String KEY_PREFIX = "sk_live_";
    private static final int KEY_ID_LENGTH = 12; // chars from the random part
    private static final SecureRandom RANDOM = new SecureRandom();

    private final ApiKeyRepository apiKeyRepository;
    private final PasswordEncoder passwordEncoder;

    /**
     * Create a new API key for a tenant.
     * Returns the full key (shown only once) and the persisted entity.
     */
    public CreateApiKeyResult createApiKey(String tenantId, String name) {
        byte[] randomBytes = new byte[24];
        RANDOM.nextBytes(randomBytes);
        String randomPart = HexFormat.of().formatHex(randomBytes);
        String fullKey = KEY_PREFIX + randomPart;
        String keyId = KEY_PREFIX + randomPart.substring(0, KEY_ID_LENGTH);

        String keyHash = passwordEncoder.encode(fullKey);

        ApiKey apiKey = ApiKey.builder()
                .keyId(keyId)
                .keyHash(keyHash)
                .tenantId(tenantId)
                .name(name != null ? name : "")
                .build();

        apiKeyRepository.save(apiKey);
        log.info("API key created: {} (tenant: {})", keyId, tenantId);

        return new CreateApiKeyResult(fullKey, apiKey);
    }

    /**
     * List all active API keys for a tenant.
     */
    public List<ApiKey> listApiKeys(String tenantId) {
        return apiKeyRepository.findByTenantIdAndIsActiveTrueOrderByCreatedAtDesc(tenantId);
    }

    /**
     * Revoke (deactivate) an API key. Returns true if found and revoked.
     */
    public boolean revokeApiKey(String keyId, String tenantId) {
        ApiKey apiKey = apiKeyRepository.findByKeyIdAndTenantId(keyId, tenantId)
                .orElse(null);
        if (apiKey == null || !apiKey.getIsActive()) {
            return false;
        }
        apiKey.setIsActive(false);
        apiKeyRepository.save(apiKey);
        log.info("API key revoked: {} (tenant: {})", keyId, tenantId);
        return true;
    }

    public record CreateApiKeyResult(String fullKey, ApiKey apiKey) {}
}
