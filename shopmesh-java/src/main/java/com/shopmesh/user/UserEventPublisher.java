package com.shopmesh.user;

import com.shopmesh.config.RabbitConfig;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.amqp.rabbit.core.RabbitTemplate;
import org.springframework.stereotype.Component;

import java.time.Instant;
import java.util.Map;

/**
 * Publishes user lifecycle events to RabbitMQ for Python Agent consumption.
 *
 * Events:
 * - user.registered: triggers vector memory initialization in Python
 * - user.profile.updated: triggers Qdrant profile sync in Python
 */
@Slf4j
@Component
@RequiredArgsConstructor
public class UserEventPublisher {

    private final RabbitTemplate rabbitTemplate;

    /**
     * Publish a user.registered event after successful registration.
     */
    public void publishUserRegistered(String userId, String tenantId, String username) {
        Map<String, Object> event = Map.of(
                "event", "user.registered",
                "user_id", userId,
                "tenant_id", tenantId,
                "username", username,
                "timestamp", Instant.now().toString()
        );

        rabbitTemplate.convertAndSend(
                RabbitConfig.EXCHANGE,
                RabbitConfig.ROUTING_KEY_USER_REGISTERED,
                event
        );
        log.info("Published user.registered event: user={}", userId);
    }

    /**
     * Publish a user.profile.updated event when user preferences change.
     */
    public void publishUserProfileUpdated(String userId, String tenantId, Map<String, Object> changes) {
        Map<String, Object> event = Map.of(
                "event", "user.profile.updated",
                "user_id", userId,
                "tenant_id", tenantId,
                "changes", changes,
                "timestamp", Instant.now().toString()
        );

        rabbitTemplate.convertAndSend(
                RabbitConfig.EXCHANGE,
                RabbitConfig.ROUTING_KEY_USER_PROFILE_UPDATED,
                event
        );
        log.info("Published user.profile.updated event: user={}", userId);
    }
}
