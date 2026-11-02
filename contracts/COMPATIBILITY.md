# Version Skew & Backward Compatibility Rules v1.0
1. **Additive Schema Updates**: Adding optional fields allows minor schema bump (`1.1`).
2. **Breaking Changes**: Bumps major schema version (`2.0`). Runtime must continue evaluating runs pinned to historical schemas.
3. **Draft vs Published**: Drafts mutate via optimistic concurrency locking. Published releases generate immutable `sha256` checksums.
