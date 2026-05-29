package com.shopmesh.apikey;

import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

import java.util.List;
import java.util.Optional;

@Repository
public interface ApiKeyRepository extends JpaRepository<ApiKey, Long> {

    Optional<ApiKey> findByKeyIdAndIsActiveTrue(String keyId);

    List<ApiKey> findByTenantIdAndIsActiveTrueOrderByCreatedAtDesc(String tenantId);

    Optional<ApiKey> findByKeyIdAndTenantId(String keyId, String tenantId);
}
