package com.shopmesh.auth;

import io.jsonwebtoken.Claims;
import io.jsonwebtoken.Jwts;
import io.jsonwebtoken.SignatureAlgorithm;
import io.jsonwebtoken.security.Keys;
import jakarta.annotation.PostConstruct;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.*;
import java.security.spec.InvalidKeySpecException;
import java.security.spec.PKCS8EncodedKeySpec;
import java.security.spec.X509EncodedKeySpec;
import java.util.*;

/**
 * JWT provider using RSA asymmetric keys (RS256).
 *
 * - Signs JWTs with the RSA private key
 * - Exposes the public key for JWKS endpoint
 * - If no key files are found, auto-generates a key pair on startup
 */
@Slf4j
@Component
public class JwtProvider {

    @Value("${shopmesh.jwt.private-key-path:config/jwt-private.pem}")
    private String privateKeyPath;

    @Value("${shopmesh.jwt.public-key-path:config/jwt-public.pem}")
    private String publicKeyPath;

    @Value("${shopmesh.jwt.access-token-expire-minutes:30}")
    private int accessTokenExpireMinutes;

    @Value("${shopmesh.jwt.refresh-token-expire-days:7}")
    private int refreshTokenExpireDays;

    @Value("${shopmesh.jwt.issuer:shopmesh-java}")
    private String issuer;

    private PrivateKey privateKey;
    private PublicKey publicKey;
    private KeyPair keyPair;

    @PostConstruct
    public void init() {
        try {
            loadOrGenerateKeys();
        } catch (Exception e) {
            log.warn("Failed to load RSA keys from files, generating ephemeral key pair: {}", e.getMessage());
            generateEphemeralKeyPair();
        }
        log.info("JWT provider initialized with RSA-2048 / RS256. Issuer: {}", issuer);
    }

    private void loadOrGenerateKeys() throws Exception {
        Path privPath = Path.of(privateKeyPath);
        Path pubPath = Path.of(publicKeyPath);

        if (Files.exists(privPath) && Files.exists(pubPath)) {
            // Load existing keys
            byte[] privBytes = Files.readAllBytes(privPath);
            byte[] pubBytes = Files.readAllBytes(pubPath);

            KeyFactory kf = KeyFactory.getInstance("RSA");
            privateKey = kf.generatePrivate(new PKCS8EncodedKeySpec(Base64.getDecoder().decode(stripPem(privBytes))));
            publicKey = kf.generatePublic(new X509EncodedKeySpec(Base64.getDecoder().decode(stripPem(pubBytes))));
            log.info("Loaded RSA keys from {} and {}", privateKeyPath, publicKeyPath);
        } else {
            // Generate and save
            generateEphemeralKeyPair();
            saveKeys(privPath, pubPath);
        }
    }

    private void generateEphemeralKeyPair() {
        keyPair = Keys.keyPairFor(SignatureAlgorithm.RS256);
        privateKey = keyPair.getPrivate();
        publicKey = keyPair.getPublic();
    }

    private void saveKeys(Path privPath, Path pubPath) {
        try {
            Files.createDirectories(privPath.getParent());
            Files.writeString(privPath, toPem("PRIVATE KEY", privateKey.getEncoded()));
            Files.writeString(pubPath, toPem("PUBLIC KEY", publicKey.getEncoded()));
            log.info("Saved RSA keys to {} and {}", privPath, pubPath);
        } catch (IOException e) {
            log.warn("Could not save RSA keys to disk: {}", e.getMessage());
        }
    }

    /**
     * Generate an access token for a user.
     */
    public String createAccessToken(String userId, String tenantId) {
        return Jwts.builder()
                .subject(userId)
                .claim("tenant_id", tenantId)
                .claim("type", "access")
                .issuer(issuer)
                .issuedAt(new Date())
                .expiration(new Date(System.currentTimeMillis() + accessTokenExpireMinutes * 60_000L))
                .signWith(privateKey, Jwts.SIG.RS256)
                .compact();
    }

    /**
     * Generate a refresh token for a user.
     */
    public String createRefreshToken(String userId, String tenantId) {
        return Jwts.builder()
                .subject(userId)
                .claim("tenant_id", tenantId)
                .claim("type", "refresh")
                .issuer(issuer)
                .issuedAt(new Date())
                .expiration(new Date(System.currentTimeMillis() + refreshTokenExpireDays * 86_400_000L))
                .signWith(privateKey, Jwts.SIG.RS256)
                .compact();
    }

    /**
     * Validate and parse a JWT token. Returns claims if valid, throws on failure.
     */
    public Claims validateToken(String token) {
        return Jwts.parser()
                .verifyWith(publicKey)
                .build()
                .parseSignedClaims(token)
                .getPayload();
    }

    /**
     * Get the RSA public key (for JWKS endpoint).
     */
    public PublicKey getPublicKey() {
        return publicKey;
    }

    /**
     * Get the key ID for JWKS (uses the public key hash).
     */
    public String getKeyId() {
        byte[] encoded = publicKey.getEncoded();
        MessageDigest digest;
        try {
            digest = MessageDigest.getInstance("SHA-256");
        } catch (NoSuchAlgorithmException e) {
            throw new RuntimeException(e);
        }
        byte[] hash = digest.digest(encoded);
        return Base64.getUrlEncoder().withoutPadding().encodeToString(Arrays.copyOf(hash, 16));
    }

    // ---------- PEM helpers ----------

    private static String stripPem(byte[] pemBytes) {
        String pem = new String(pemBytes);
        return pem.replaceAll("-----BEGIN .*-----", "")
                .replaceAll("-----END .*-----", "")
                .replaceAll("\\s+", "");
    }

    private static String toPem(String type, byte[] encoded) {
        String b64 = Base64.getEncoder().encodeToString(encoded);
        StringBuilder sb = new StringBuilder();
        sb.append("-----BEGIN ").append(type).append("-----\n");
        for (int i = 0; i < b64.length(); i += 64) {
            sb.append(b64, i, Math.min(i + 64, b64.length())).append('\n');
        }
        sb.append("-----END ").append(type).append("-----\n");
        return sb.toString();
    }
}
