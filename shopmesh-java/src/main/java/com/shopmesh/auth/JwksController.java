package com.shopmesh.auth;

import com.fasterxml.jackson.databind.ObjectMapper;
import lombok.RequiredArgsConstructor;
import org.springframework.http.MediaType;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

import java.math.BigInteger;
import java.security.interfaces.RSAPublicKey;
import java.util.Base64;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * JWKS (JSON Web Key Set) endpoint.
 * Exposes the RSA public key so that other services (e.g., Python Agent)
 * can verify JWTs signed by this service.
 *
 * GET /.well-known/jwks.json
 */
@RestController
@RequiredArgsConstructor
public class JwksController {

    private final JwtProvider jwtProvider;

    @GetMapping(value = "/.well-known/jwks.json", produces = MediaType.APPLICATION_JSON_VALUE)
    public Map<String, Object> jwks() {
        RSAPublicKey publicKey = (RSAPublicKey) jwtProvider.getPublicKey();

        Map<String, Object> key = new LinkedHashMap<>();
        key.put("kty", "RSA");
        key.put("use", "sig");
        key.put("alg", "RS256");
        key.put("kid", jwtProvider.getKeyId());
        key.put("n", base64UrlEncode(publicKey.getModulus()));
        key.put("e", base64UrlEncode(publicKey.getPublicExponent()));

        Map<String, Object> response = new LinkedHashMap<>();
        response.put("keys", List.of(key));
        return response;
    }

    private static String base64UrlEncode(BigInteger value) {
        return Base64.getUrlEncoder().withoutPadding()
                .encodeToString(value.toByteArray());
    }
}
