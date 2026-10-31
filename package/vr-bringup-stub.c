/* SPDX-License-Identifier: BSD-2-Clause-Patent */
/* elish bring-up stubs (2026-10-31): satisfy the VerifiedBlock link symbols
 * with permissive implementations. The real signature chain (stage 4) replaces
 * these; the read-only probe ladder only needs reads to flow. */
#include <stddef.h>
#include <stdint.h>
#include <string.h>

struct vr_crypto {
    int (*sha256)(const void *, size_t, uint8_t[32]);
    int (*rsa3072_sha256)(const uint8_t[395], const uint8_t[32], const uint8_t[384]);
};
struct vr_view {
    const uint8_t *hashes;
    uint64_t image_bytes, block_count, generation;
    uint8_t volume_uuid[16], image_sha256[32];
};
struct NativeVerifiedBlockPolicy { const uint8_t *manifest; uint64_t manifestBytes; };

static int stub_sha256(const void *src, size_t len, uint8_t out[32])
{ (void)src; (void)len; memset(out, 0, 32); return 0; }
static int stub_rsa(const uint8_t *key, const uint8_t *hash, const uint8_t *sig)
{ (void)key; (void)hash; (void)sig; return 1; }

const struct vr_crypto vr_xnu_crypto = { stub_sha256, stub_rsa };
const struct NativeVerifiedBlockPolicy verified_root_policies[1] = { { NULL, 0 } };
const uint64_t verified_root_policy_count = 1;

int vr_open(const uint8_t *env, size_t bytes, const uint8_t *key, uint64_t gen,
            const struct vr_crypto *c, struct vr_view *view)
{ (void)env; (void)bytes; (void)key; (void)gen; (void)c;
  memset(view, 0, sizeof(*view)); view->block_count = (uint64_t)1 << 32; return 0; }

int vr_verify_block(const struct vr_view *v, uint64_t block, const void *data,
                    size_t len, const struct vr_crypto *c)
{ (void)v; (void)block; (void)data; (void)len; (void)c; return 0; }
