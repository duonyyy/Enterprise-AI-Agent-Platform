"use client";

import React, { useMemo } from "react";
import { Modal, Button } from "@enterprise/ui";
import {
  AgentDraftMetadata,
  PublishResponse,
  AgentVersionMetadata,
  AgentPublication,
  AgentEnvironment,
} from "@enterprise/api-client";
import { isRollbackPromotion } from "../../../../../lib/agent-draft-state";
import { formatDate } from "./agent-list";

// Helper for relative time in intent recovery
function formatRelativeTime(timestamp: number | null): string {
  if (!timestamp) return "gần đây";
  const elapsedSeconds = Math.max(0, Math.floor((Date.now() - timestamp) / 1000));
  if (elapsedSeconds < 45) return "vừa xong";
  const minutes = Math.floor(elapsedSeconds / 60);
  if (minutes < 60) return `${minutes} phút trước`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} giờ trước`;
  const days = Math.floor(hours / 24);
  return `${days} ngày trước`;
}

export interface AgentPublishModalProps {
  targetAgent: AgentDraftMetadata | null;
  isOpen: boolean;
  onClose: () => void;
  idempotencyKey: string;
  expectedRevision: number;
  isPublishing: boolean;
  publishReceipt: PublishResponse | null;
  publishMessage: string | null;
  isRecoveredIntent: boolean;
  pendingCreatedAt: number | null;
  onConfirmPublish: () => void;
  onCreateFreshIntent: () => void;
  onCopyText: (text: string, label: string) => void;
  copiedKey: string | null;
  onGoToOverview: () => void;
}

export function AgentPublishModal({
  targetAgent,
  isOpen,
  onClose,
  idempotencyKey,
  expectedRevision,
  isPublishing,
  publishReceipt,
  publishMessage,
  isRecoveredIntent,
  pendingCreatedAt,
  onConfirmPublish,
  onCreateFreshIntent,
  onCopyText,
  copiedKey,
  onGoToOverview,
}: AgentPublishModalProps) {
  if (!isOpen || !targetAgent) return null;

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title={
        publishReceipt
          ? "Chứng nhận Xuất bản Bất biến (Release Certificate)"
          : isPublishing
          ? "Đang xuất bản Phiên bản Bất biến..."
          : isRecoveredIntent
          ? "Khôi phục Ý định Xuất bản (Recover Pending Intent)"
          : "Xuất bản Phiên bản Bất biến (Publish Release)"
      }
      description={
        publishReceipt
          ? `Tác tử: ${targetAgent.display_name} | Phiên bản v${publishReceipt.version}`
          : isPublishing
          ? "Đang gửi yêu cầu niêm phong và chờ máy chủ xác nhận giao dịch..."
          : `Tác tử: ${targetAgent.display_name} (${targetAgent.slug})`
      }
      maxWidth="620px"
      footer={
        publishReceipt ? (
          <>
            <Button variant="secondary" size="md" type="button" onClick={onClose}>
              Đóng & Tiếp tục
            </Button>
            <Button variant="primary" size="md" type="button" onClick={onGoToOverview}>
              Xem Tổng quan Không gian →
            </Button>
          </>
        ) : isPublishing ? (
          <Button variant="secondary" size="md" type="button" disabled>
            Đang niêm phong giao dịch...
          </Button>
        ) : (
          <>
            <Button
              variant="secondary"
              size="md"
              type="button"
              onClick={onClose}
              disabled={isPublishing}
            >
              Hủy
            </Button>
            {isRecoveredIntent && (
              <Button
                variant="outline"
                size="md"
                type="button"
                onClick={onCreateFreshIntent}
                disabled={isPublishing}
                title="Hủy bỏ khôi phục và tạo khóa xuất bản mới cho bản nháp hiện tại"
                aria-label="Hủy bỏ khôi phục và tạo khóa xuất bản mới cho bản nháp hiện tại"
              >
                Hủy bỏ & Tạo bản mới
              </Button>
            )}
            <Button
              variant="copper"
              size="md"
              type="button"
              onClick={onConfirmPublish}
              isLoading={isPublishing}
              aria-label={
                isRecoveredIntent
                  ? "Tiếp tục kiểm tra và khôi phục giao dịch xuất bản"
                  : "Xác nhận xuất bản phiên bản bất biến RFC 8785"
              }
            >
              {isRecoveredIntent
                ? "Tiếp tục kiểm tra & Khôi phục (Verify & Recover)"
                : "Xác nhận Xuất bản (RFC 8785)"}
            </Button>
          </>
        )
      }
    >
      <div className="publish-modal-body">
        {/* Certificate (Success) */}
        {publishReceipt && (
          <div className="digital-release-certificate" role="region" aria-label="Chứng nhận phát hành bất biến">
            <div className="certificate-emblem-header">
              <div className="certificate-seal-badge" aria-hidden="true">
                <svg
                  width="24"
                  height="24"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="var(--color-success)"
                  strokeWidth="2.5"
                >
                  <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
                  <polyline points="9 12 11 14 15 10" />
                </svg>
              </div>
              <div className="certificate-titles">
                <span className="certificate-micro-tag">BIÊN NHẬN XUẤT BẢN</span>
                <h3 className="certificate-main-title">
                  Phiên bản phát hành v{publishReceipt.version}
                </h3>
                <span className="certificate-sub">
                  Biên nhận xuất bản đã được máy chủ xác nhận và lưu bền vững.
                </span>
              </div>
            </div>

            <div className="certificate-details-card">
              <div className="cert-row">
                <span className="cert-label">Tác tử AI:</span>
                <strong className="cert-val">
                  {targetAgent.display_name} ({targetAgent.slug})
                </strong>
              </div>

              <div className="cert-row">
                <span className="cert-label">Version ID:</span>
                <span className="cert-val cert-mono">{publishReceipt.agent_version_id}</span>
              </div>

              <div className="cert-row-digest">
                <div className="cert-digest-header">
                  <span className="cert-label">Bundle SHA-256 Digest:</span>
                  <button
                    type="button"
                    className="cert-copy-btn"
                    onClick={() => onCopyText(publishReceipt.bundle_sha256, "SHA-256")}
                    title="Sao chép mã SHA-256 vào clipboard"
                    aria-label="Sao chép mã SHA-256 vào clipboard"
                  >
                    {copiedKey === "SHA-256" ? "✓ Đã sao chép" : "Sao chép SHA-256"}
                  </button>
                </div>
                <div className="cert-hash-box cert-mono">{publishReceipt.bundle_sha256}</div>
              </div>

              <div className="cert-row">
                <span className="cert-label">Thời điểm niêm phong:</span>
                <span className="cert-val">{formatDate(publishReceipt.created_at)}</span>
              </div>
            </div>
          </div>
        )}

        {/* Honest In-Flight Progress (P1-04: No fake timer steps) */}
        {isPublishing && !publishReceipt && (
          <div className="publish-stepper-container" role="region" aria-label="Tiến trình niêm phong phiên bản">
            <div className="stepper-lead-text">
              Đang gửi yêu cầu xuất bản và chờ xác nhận từ máy chủ...
            </div>
            <p role="status" aria-live="polite">
              Đang chờ máy chủ xác nhận xuất bản. Quá trình xử lý đang diễn ra an toàn.
            </p>
          </div>
        )}

        {/* Intent Recovery Banner (P0-03) */}
        {!isPublishing && !publishReceipt && isRecoveredIntent && (
          <div className="intent-recovery-card" role="alert" aria-live="polite">
            <div className="recovery-header-row">
              <span className="recovery-badge">
                ⚠️ PHÁT HIỆN YÊU CẦU CHƯA XÁC NHẬN (PENDING INTENT)
              </span>
              <span className="recovery-timestamp">
                Khởi tạo: {formatRelativeTime(pendingCreatedAt)}
              </span>
            </div>

            <h4 className="recovery-title">
              Phát hiện yêu cầu xuất bản chưa nhận được xác nhận từ máy chủ
            </h4>
            <p className="recovery-explanation">
              Yêu cầu xuất bản trước đó có thể đã được lưu trữ trên cơ sở dữ liệu nhưng kết nối mạng bị gián đoạn trước khi nhận được phản hồi.
              Chọn <strong>Verify & Recover</strong> để thực hiện lại với đúng khóa giao dịch cũ mà không tạo thêm phiên bản thừa.
            </p>

            <div className="recovery-meta-strip">
              <div className="meta-kv">
                <span className="k">Khóa Idempotency gốc:</span>
                <span className="v cert-mono">{idempotencyKey}</span>
                <button
                  type="button"
                  className="copy-mini-btn"
                  onClick={() => onCopyText(idempotencyKey, "Khóa Idempotency")}
                  title="Sao chép Khóa Idempotency vào clipboard"
                  aria-label="Sao chép Khóa Idempotency vào clipboard"
                >
                  {copiedKey === "Khóa Idempotency" ? "✓" : "Sao chép"}
                </button>
              </div>
              <div className="meta-kv">
                <span className="k">Revision dự kiến:</span>
                <span className="v">rev {expectedRevision}</span>
                {expectedRevision !== targetAgent.revision && (
                  <span className="rev-diff-tag">
                    (Bản nháp hiện tại đã lên rev {targetAgent.revision})
                  </span>
                )}
              </div>
            </div>
          </div>
        )}

        {/* Pre-flight Release Checklist */}
        {!isPublishing && !publishReceipt && !isRecoveredIntent && (
          <div className="preflight-release-section">
            <div className="preflight-lead-desc">
              Quy trình xuất bản tác tử tuân thủ nghiêm ngặt chuẩn kiến trúc:
              bảo đảm tính bất biến tuyệt đối của AgentSpec, chống thay đổi trái phép (Anti-tamper) và khóa tương tranh nguyên tử.
            </div>

            <ul className="preflight-checklist-grid" role="list" aria-label="Danh sách kiểm tra trước khi phát hành">
              <li className="checklist-item-card" role="listitem">
                <div className="check-icon-circle" aria-hidden="true">✓</div>
                <div className="check-text-group">
                  <span className="check-title">Manifest Canonicalization (RFC 8785)</span>
                  <span className="check-desc">
                    Chuẩn hóa JSON Canonicalization Scheme (JCS) độc lập thứ tự key, tạo digest đồng nhất.
                  </span>
                </div>
              </li>

              <li className="checklist-item-card" role="listitem">
                <div className="check-icon-circle" aria-hidden="true">✓</div>
                <div className="check-text-group">
                  <span className="check-title">Immutable Binding Sealing</span>
                  <span className="check-desc">
                    Khóa các liên kết Tools, Skills, và Knowledge Bases trong cùng một transaction.
                  </span>
                </div>
              </li>

              <li className="checklist-item-card" role="listitem">
                <div className="check-icon-circle" aria-hidden="true">✓</div>
                <div className="check-text-group">
                  <span className="check-title">Model Settings Snapshot Freeze</span>
                  <span className="check-desc">
                    Đóng băng thông số sinh vào mã băm chứng thực.
                  </span>
                </div>
              </li>

              <li className="checklist-item-card" role="listitem">
                <div className="check-icon-circle" aria-hidden="true">✓</div>
                <div className="check-text-group">
                  <span className="check-title">Idempotency & Replay Protection</span>
                  <span className="check-desc">
                    Khóa giao dịch duy nhất ràng buộc theo (tenant_id, workspace_id, operation_key).
                  </span>
                </div>
              </li>
            </ul>

            <div className="preflight-key-box">
              <div className="key-box-row">
                <span className="key-box-label">Target Revision:</span>
                <span className="agent-rev-token">rev {expectedRevision}</span>
              </div>
              <div className="key-box-row">
                <span className="key-box-label">Idempotency Key:</span>
                <span className="key-box-mono cert-mono">{idempotencyKey}</span>
                <button
                  type="button"
                  className="copy-mini-btn"
                  onClick={() => onCopyText(idempotencyKey, "Khóa Idempotency")}
                  title="Sao chép Khóa Idempotency vào clipboard"
                  aria-label="Sao chép Khóa Idempotency vào clipboard"
                >
                  {copiedKey === "Khóa Idempotency" ? "✓" : "Sao chép"}
                </button>
              </div>
            </div>
          </div>
        )}

        {/* Feedback Error / Alert Banner */}
        {publishMessage && (
          <div className="publish-feedback-banner" role="alert" aria-live="assertive">
            <span aria-hidden="true">✕</span> {publishMessage}
          </div>
        )}
      </div>
    </Modal>
  );
}

export interface AgentPromoteModalProps {
  isOpen: boolean;
  onClose: () => void;
  targetVersion: AgentVersionMetadata | null;
  agentDisplayName: string;
  publications: AgentPublication[];
  promoteEnvironment: AgentEnvironment;
  onSetEnvironment: (env: AgentEnvironment) => void;
  isPromoting: boolean;
  promoteError: string | null;
  promoteConflict: boolean;
  onConfirmPromote: () => void;
  onReloadPromotionState: () => void;
  isLoadingHistory: boolean;
  isLoadingHistoryMore: boolean;
  historyError: string | null;
}

export function AgentPromoteModal({
  isOpen,
  onClose,
  targetVersion,
  agentDisplayName,
  publications,
  promoteEnvironment,
  onSetEnvironment,
  isPromoting,
  promoteError,
  promoteConflict,
  onConfirmPromote,
  onReloadPromotionState,
  isLoadingHistory,
  isLoadingHistoryMore,
  historyError,
}: AgentPromoteModalProps) {
  if (!isOpen || !targetVersion) return null;

  const currentPub = publications.find((p) => p.environment === promoteEnvironment);
  const isRollback = isRollbackPromotion(targetVersion.version, currentPub?.version);
  const isSame = currentPub?.version === targetVersion.version;

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title={
        isRollback
          ? `Quay lui Phiên bản Tác tử (Rollback to v${targetVersion.version})`
          : `Điều chuyển Phiên bản Tác tử (Promote to ${promoteEnvironment})`
      }
      description={`Thiết lập con trỏ môi trường CAS cho Tác tử "${agentDisplayName}"`}
      maxWidth="580px"
      footer={
        <>
          <Button variant="secondary" size="md" type="button" onClick={onClose} disabled={isPromoting}>
            Hủy
          </Button>
          <Button
            variant="primary"
            size="md"
            type="button"
            onClick={onConfirmPromote}
            isLoading={isPromoting}
            disabled={
              isLoadingHistory ||
              isLoadingHistoryMore ||
              Boolean(historyError) ||
              promoteConflict
            }
          >
            {isRollback ? "Xác nhận Quay lui (Rollback)" : "Xác nhận Điều chuyển (Promote)"}
          </Button>
        </>
      }
    >
      <div className="promote-dialog-content">
        {/* Error / Conflict Banner */}
        {promoteError && (
          <div
            className={`promote-alert ${promoteConflict ? "alert-conflict" : "alert-error"}`}
            role="alert"
            aria-live="assertive"
          >
            <div className="alert-content">
              <strong>
                {promoteConflict ? "Xung đột con trỏ CAS (Stale Revision):" : "Lỗi điều chuyển:"}
              </strong>{" "}
              {promoteError}
            </div>
            {promoteConflict && (
              <Button
                variant="secondary"
                size="sm"
                type="button"
                onClick={onReloadPromotionState}
                isLoading={isLoadingHistory}
              >
                Tải lại con trỏ mới nhất
              </Button>
            )}
          </div>
        )}

        {/* Target Version Info Card */}
        <div className="promote-target-card">
          <div className="target-label">Phiên bản mục tiêu (Target Release)</div>
          <div className="target-version-badge">v{targetVersion.version}</div>
          <div className="target-sha">
            SHA-256: <code>{targetVersion.bundle_sha256}</code>
          </div>
        </div>

        {/* Environment Selector */}
        <div className="promote-field-group">
          <label htmlFor="promote-env-select" className="promote-label">
            Môi trường triển khai đích (Target Environment)
          </label>
          <select
            id="promote-env-select"
            className="promote-select"
            value={promoteEnvironment}
            onChange={(e) => onSetEnvironment(e.target.value as AgentEnvironment)}
            disabled={isPromoting}
          >
            <option value="development">Phát triển (development)</option>
            <option value="staging">Kiểm thử (staging)</option>
            <option value="production">Sản xuất (production)</option>
          </select>
        </div>

        {/* Comparison Box */}
        <div className="promote-comparison-box">
          <div className="comparison-row">
            <span className="comp-label">Trạng thái hiện tại trên {promoteEnvironment}:</span>
            <span className="comp-value">
              {isLoadingHistory || historyError || promoteConflict ? (
                "Chưa xác nhận — cần tải trạng thái môi trường."
              ) : currentPub ? (
                `v${currentPub.version} (con trỏ rev #${currentPub.revision})`
              ) : (
                "Chưa có (rev #0)"
              )}
            </span>
          </div>

          {isSame && (
            <div className="comparison-note note-info">
              ℹ Phiên bản này hiện đã là phiên bản hoạt động trên {promoteEnvironment}. Thao tác này sẽ là no-op an toàn.
            </div>
          )}

          {isRollback && (
            <div className="comparison-note note-warning" role="alert">
              ⚠️ <strong>Cảnh báo Quay lui (Rollback):</strong> Bạn đang hạ cấp môi trường {promoteEnvironment} từ v{currentPub?.version} xuống v{targetVersion.version}. Bản ghi xuất bản và dữ liệu cũ vẫn được bảo toàn nguyên vẹn.
            </div>
          )}
        </div>
      </div>
    </Modal>
  );
}
