package com.shopmesh.apikey;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.shopmesh.auth.dto.RegisterRequest;
import org.junit.jupiter.api.Test;
import org.springframework.amqp.rabbit.core.RabbitTemplate;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;

import java.util.UUID;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@SpringBootTest
@AutoConfigureMockMvc
@ActiveProfiles("test")
class ApiKeyControllerTest {

    @MockitoBean
    private RabbitTemplate rabbitTemplate;

    @Autowired
    private MockMvc mockMvc;

    @Autowired
    private ObjectMapper objectMapper;

    private String registerUser(String username) throws Exception {
        RegisterRequest regReq = new RegisterRequest();
        regReq.setUsername(username);
        regReq.setPassword("pass123");

        MvcResult result = mockMvc.perform(post("/api/auth/register")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(regReq)))
                .andExpect(status().isCreated())
                .andReturn();

        return objectMapper.readTree(result.getResponse().getContentAsString())
                .get("accessToken").asText();
    }

    @Test
    void createApiKey() throws Exception {
        String token = registerUser("ak_create");

        CreateApiKeyRequest req = new CreateApiKeyRequest();
        req.setName("Test Integration");

        mockMvc.perform(post("/api/auth/api-keys")
                        .header("Authorization", "Bearer " + token)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(req)))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.apiKey").value(org.hamcrest.Matchers.startsWith("sk_live_")))
                .andExpect(jsonPath("$.keyId").value(org.hamcrest.Matchers.startsWith("sk_live_")))
                .andExpect(jsonPath("$.name").value("Test Integration"))
                .andExpect(jsonPath("$.message").isNotEmpty());
    }

    @Test
    void listApiKeys() throws Exception {
        String token = registerUser("ak_list");

        CreateApiKeyRequest req1 = new CreateApiKeyRequest();
        req1.setName("Key 1");
        CreateApiKeyRequest req2 = new CreateApiKeyRequest();
        req2.setName("Key 2");

        mockMvc.perform(post("/api/auth/api-keys")
                        .header("Authorization", "Bearer " + token)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(req1)))
                .andExpect(status().isCreated());

        mockMvc.perform(post("/api/auth/api-keys")
                        .header("Authorization", "Bearer " + token)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(req2)))
                .andExpect(status().isCreated());

        mockMvc.perform(get("/api/auth/api-keys")
                        .header("Authorization", "Bearer " + token))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.length()").value(2));
    }

    @Test
    void revokeApiKey() throws Exception {
        String token = registerUser("ak_revoke");

        CreateApiKeyRequest req = new CreateApiKeyRequest();
        req.setName("To Revoke");

        MvcResult result = mockMvc.perform(post("/api/auth/api-keys")
                        .header("Authorization", "Bearer " + token)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(req)))
                .andExpect(status().isCreated())
                .andReturn();

        String keyId = objectMapper.readTree(result.getResponse().getContentAsString())
                .get("keyId").asText();

        mockMvc.perform(delete("/api/auth/api-keys/" + keyId)
                        .header("Authorization", "Bearer " + token))
                .andExpect(status().isNoContent());

        mockMvc.perform(get("/api/auth/api-keys")
                        .header("Authorization", "Bearer " + token))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.length()").value(0));
    }

    @Test
    void createApiKeyRequiresAuth() throws Exception {
        CreateApiKeyRequest req = new CreateApiKeyRequest();
        req.setName("No Auth");

        mockMvc.perform(post("/api/auth/api-keys")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(req)))
                .andExpect(status().isForbidden());
    }

    @Test
    void revokeNonexistentKey() throws Exception {
        String token = registerUser("ak_nonexist");

        mockMvc.perform(delete("/api/auth/api-keys/sk_live_nonexistent")
                        .header("Authorization", "Bearer " + token))
                .andExpect(status().isNotFound());
    }
}
