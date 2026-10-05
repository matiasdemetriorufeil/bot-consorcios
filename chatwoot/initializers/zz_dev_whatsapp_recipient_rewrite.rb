# ⚠️ DEVELOPMENT ONLY. Mounted read-only into chatwoot-rails and chatwoot-sidekiq by
# docker-compose.yml (see docs/whatsapp.md, "Números argentinos con el número de prueba").
#
# Meta's test number only sends to its allowed-recipients list. For Argentina, WhatsApp gives the
# wa_id as 549 + area + number, but the list only accepts 54 + area + 15 + number, so replies fail
# with 131030 "Recipient phone number not in allowed list". This rewrites the recipient right
# before Chatwoot calls the Cloud API.
#
# CHATWOOT_DEV_WA_RECIPIENT_REWRITE="from:to,from:to" (digits exactly as Chatwoot sends them,
# no "+"). Empty or unset: does nothing. Only exact matches are rewritten.
#
# Checked against Chatwoot v4.18.0: every message goes through these two public methods of
# Whatsapp::Providers::WhatsappCloudService (text, attachments and interactive messages are
# dispatched by send_message):
#   send_message(phone_number, message)
#   send_template(phone_number, template_info, message)

module DevWhatsappRecipientRewrite
  ENV_VAR = 'CHATWOOT_DEV_WA_RECIPIENT_REWRITE'.freeze

  def self.parse(raw)
    raw.to_s.split(',').each_with_object({}) do |pair, rewrites|
      from, to = pair.split(':', 2).map { |part| part.to_s.strip }
      if from.blank? || to.blank?
        Rails.logger.warn("[DEV_WA_RECIPIENT_REWRITE] ignoring malformed pair in #{ENV_VAR}")
        next
      end
      rewrites[from] = to
    end.freeze
  end

  REWRITES = parse(ENV.fetch(ENV_VAR, ''))

  def self.rewrite(phone_number)
    to = REWRITES[phone_number.to_s]
    return phone_number if to.nil?

    Rails.logger.info("[DEV_WA_RECIPIENT_REWRITE] recipient #{phone_number} -> #{to}")
    to
  end

  module CloudService
    def send_message(phone_number, message)
      super(DevWhatsappRecipientRewrite.rewrite(phone_number), message)
    end

    def send_template(phone_number, template_info, message)
      super(DevWhatsappRecipientRewrite.rewrite(phone_number), template_info, message)
    end
  end
end

if DevWhatsappRecipientRewrite::REWRITES.any?
  Rails.application.config.to_prepare do
    klass = Whatsapp::Providers::WhatsappCloudService
    klass.prepend(DevWhatsappRecipientRewrite::CloudService) unless klass <= DevWhatsappRecipientRewrite::CloudService
  end
  Rails.logger.info(
    "[DEV_WA_RECIPIENT_REWRITE] enabled for #{DevWhatsappRecipientRewrite::REWRITES.size} recipient(s)"
  )
end
