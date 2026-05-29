package com.shopmesh.user;

import com.shopmesh.config.RabbitConfig;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.amqp.rabbit.core.RabbitTemplate;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.context.bean.override.mockito.MockitoBean;

import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.*;

@SpringBootTest
@ActiveProfiles("test")
class UserEventPublisherTest {

    @MockitoBean
    private RabbitTemplate rabbitTemplate;

    @Autowired
    private UserEventPublisher userEventPublisher;

    @SuppressWarnings("unchecked")
    @Test
    void publishUserRegistered() {
        userEventPublisher.publishUserRegistered("user-123", "tenant-456", "testuser");

        ArgumentCaptor<Map<String, Object>> captor = ArgumentCaptor.forClass(Map.class);
        verify(rabbitTemplate).convertAndSend(
                eq(RabbitConfig.EXCHANGE),
                eq(RabbitConfig.ROUTING_KEY_USER_REGISTERED),
                captor.capture()
        );

        Map<String, Object> event = captor.getValue();
        assertThat(event.get("event")).isEqualTo("user.registered");
        assertThat(event.get("user_id")).isEqualTo("user-123");
        assertThat(event.get("tenant_id")).isEqualTo("tenant-456");
        assertThat(event.get("username")).isEqualTo("testuser");
        assertThat(event).containsKey("timestamp");
    }

    @SuppressWarnings("unchecked")
    @Test
    void publishUserProfileUpdated() {
        Map<String, Object> changes = Map.of("preferred_brands", "added:BrandX");

        userEventPublisher.publishUserProfileUpdated("user-123", "tenant-456", changes);

        ArgumentCaptor<Map<String, Object>> captor = ArgumentCaptor.forClass(Map.class);
        verify(rabbitTemplate).convertAndSend(
                eq(RabbitConfig.EXCHANGE),
                eq(RabbitConfig.ROUTING_KEY_USER_PROFILE_UPDATED),
                captor.capture()
        );

        Map<String, Object> event = captor.getValue();
        assertThat(event.get("event")).isEqualTo("user.profile.updated");
        assertThat(event.get("user_id")).isEqualTo("user-123");
        assertThat(event.get("tenant_id")).isEqualTo("tenant-456");
        assertThat(event).containsKey("changes");
        assertThat(event).containsKey("timestamp");
    }
}
