package com.shopmesh.config;

import org.springframework.amqp.core.*;
import org.springframework.amqp.rabbit.connection.ConnectionFactory;
import org.springframework.amqp.rabbit.core.RabbitTemplate;
import org.springframework.amqp.support.converter.Jackson2JsonMessageConverter;
import org.springframework.amqp.support.converter.MessageConverter;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
public class RabbitConfig {

    public static final String EXCHANGE = "shopmesh.events";
    public static final String QUEUE_USER_EVENTS = "events";
    public static final String ROUTING_KEY_USER_REGISTERED = "user.registered";
    public static final String ROUTING_KEY_USER_PROFILE_UPDATED = "user.profile.updated";

    @Bean
    public DirectExchange eventsExchange() {
        return new DirectExchange(EXCHANGE);
    }

    @Bean
    public Queue eventsQueue() {
        return QueueBuilder.durable(QUEUE_USER_EVENTS).build();
    }

    @Bean
    public Binding userRegisteredBinding(Queue eventsQueue, DirectExchange eventsExchange) {
        return BindingBuilder.bind(eventsQueue)
                .to(eventsExchange)
                .with(ROUTING_KEY_USER_REGISTERED);
    }

    @Bean
    public Binding userProfileUpdatedBinding(Queue eventsQueue, DirectExchange eventsExchange) {
        return BindingBuilder.bind(eventsQueue)
                .to(eventsExchange)
                .with(ROUTING_KEY_USER_PROFILE_UPDATED);
    }

    @Bean
    public MessageConverter jsonMessageConverter() {
        return new Jackson2JsonMessageConverter();
    }

    @Bean
    public RabbitTemplate rabbitTemplate(ConnectionFactory connectionFactory) {
        RabbitTemplate template = new RabbitTemplate(connectionFactory);
        template.setMessageConverter(jsonMessageConverter());
        return template;
    }
}
