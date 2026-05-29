package com.shopmesh.apikey;

import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class CreateApiKeyResponse {

    private String keyId;
    private String name;
    private String apiKey;  // full key, shown only once
    private String message;
}
